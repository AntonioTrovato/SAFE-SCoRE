"""
scenic_carla_runner.py

Executes .scenic scenario files on CARLA and produces base-log JSON files
compatible with the rest of the SAFE-SCoRE pipeline (same shape as the logs
produced by external tools via data_gathering.carlaBasicLogger, see
docs/integration.md / docs/base_log_json.md).

Default engine drives the ego with Scenic's own compiled `behavior` (e.g.
`EgoBehavior()` in the sample .scenic files) via scenic.simulators.carla -
no separate CARLA BehaviorAgent needed. The "autoware" engine only changes
which CARLA server address/port we connect to (see the plan/README note on
this being a deliberate simplification: the real Autoware bridge contract
is out of scope for this stage-1 tool).
"""

from __future__ import annotations

import contextlib
import importlib
import logging
import os
import re
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
for _p in (REPO_ROOT, SRC_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import carla  # noqa: E402
import scenic  # noqa: E402
from scenic.simulators.carla import CarlaSimulator  # noqa: E402

from runner.recorder import RunnerContext, on_monitor_step  # noqa: E402

log = logging.getLogger("ScenicCarlaRunner")

_MAP_PARAM_RE = re.compile(
    r"param\s+map\s*=\s*localPath\((['\"])(?P<path>.*?)\1\)"
)

# Scenario formats _convert_to_scenic() knows how to turn into .scenic.
_CONVERTIBLE_SUFFIXES = {".xosc"}

# Stock CARLA towns are loaded by name; anything else has to be ingested as
# raw OpenDRIVE (see _carla_town_from_scenic).
_CARLA_TOWN_RE = re.compile(r"^Town\d+", re.IGNORECASE)

# Mesh settings used when a scenario's map has to be ingested as raw
# OpenDRIVE. CARLA's defaults (vertex_distance 2.0, wall_height 1.0,
# additional_width 0.6) leave gaps in the generated surface that *driving*
# vehicles fall through while stationary ones stay put - tune these if
# actors still fall through the road.
OPENDRIVE_MESH_PARAMS = {
    "vertex_distance": 1.0,        # finer tessellation (default 2.0)
    "max_road_length": 50.0,
    "wall_height": 3.0,            # taller side barriers (default 1.0)
    "additional_width": 3.0,       # wider drivable margin (default 0.6)
    "smooth_junctions": True,
    "enable_mesh_visibility": True,
}


@contextlib.contextmanager
def _tuned_opendrive_generation(params: dict):
    """Make Scenic's parameterless OpenDRIVE ingest use `params`.

    CarlaSimulator hardcodes `client.generate_opendrive_world(data)`, so it
    always takes CARLA's default mesh settings and offers no way to pass an
    OpendriveGenerationParameters. Rather than reimplement its constructor,
    the client call is wrapped for the duration of that construction only,
    and restored afterwards.
    """
    original = carla.Client.generate_opendrive_world
    generation_params = carla.OpendriveGenerationParameters(**params)

    def _patched(client, opendrive, parameters=None, reset_settings=True):
        return original(client, opendrive, generation_params, reset_settings)

    carla.Client.generate_opendrive_world = _patched
    try:
        yield
    finally:
        carla.Client.generate_opendrive_world = original

_MONITOR_SNIPPET = """

from runner.recorder import on_monitor_step

monitor SafeScoreRecorder():
    while True:
        on_monitor_step(globalParameters._ss_ctx, ego)
        wait

require monitor SafeScoreRecorder()
"""


def _default_timeout_s(scenic_text: str, fallback: float = 60.0) -> float:
    """Scenario duration from its `terminate after N seconds` statement.

    The duration may be written either as a literal or as a module-level
    constant (the .xosc converter emits `SIM_DURATION = 165` followed by
    `terminate after SIM_DURATION seconds`), so a symbolic name is resolved
    against its assignment before falling back.
    """
    m = re.search(r"terminate\s+after\s+(\w+(?:\.\d+)?)\s+seconds", scenic_text)
    if not m:
        return fallback

    value = m.group(1)
    try:
        return float(value)
    except ValueError:
        pass

    const = re.search(
        rf"^\s*{re.escape(value)}\s*=\s*(\d+(?:\.\d+)?)", scenic_text, re.MULTILINE
    )
    return float(const.group(1)) if const else fallback


def _prepare_temp_scenic(scenic_path: Path, tmp_dir: Path) -> Path:
    """Copy scenic_path into tmp_dir with its `param map = localPath(...)`
    rewritten to an absolute path (so the copy can live anywhere) and the
    recorder monitor appended."""
    text = scenic_path.read_text(encoding="utf-8")

    def _rewrite_map(match: re.Match) -> str:
        quote = match.group(1)
        rel = match.group("path")
        abs_path = (scenic_path.parent / rel).resolve()
        return f"param map = localPath({quote}{abs_path.as_posix()}{quote})"

    text = _MAP_PARAM_RE.sub(_rewrite_map, text, count=1)
    text += _MONITOR_SNIPPET

    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / scenic_path.name
    tmp_path.write_text(text, encoding="utf-8")
    return tmp_path


# Which converter turns a .xosc into a .scenic, as "module:function".
#
# The function must take (input_path, output_path) as strings and return
# the path it actually wrote. Swap converters either by editing this
# default or, without touching the code, by setting the environment
# variable - handy for A/B-ing two converters over the same input:
#
#     SAFE_SCORE_XOSC_CONVERTER=mypkg.my_converter:convert
#
DEFAULT_XOSC_CONVERTER = "converter.CARLA_converter:convert_file"


def resolve_converter(spec: Optional[str] = None):
    """Import the configured converter and return (function, spec_used).

    Raises if the spec cannot be resolved, so a typo surfaces immediately
    instead of looking like "there is no converter".
    """
    spec = spec or os.environ.get("SAFE_SCORE_XOSC_CONVERTER") or DEFAULT_XOSC_CONVERTER
    if ":" not in spec:
        raise ValueError(f"Converter spec must be 'module:function', got {spec!r}")
    module_name, _, func_name = spec.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, func_name), spec


def _convert_to_scenic(src: Path, dest: Path) -> Optional[Path]:
    """
    Read `src` (a non-.scenic scenario file) and write an equivalent
    .scenic file at `dest`. Returns the written path on success, or None
    if the format isn't supported / conversion fails, so the caller can
    skip it with a warning rather than crash the whole suite.

    The converter is pluggable - see DEFAULT_XOSC_CONVERTER. A converter
    emitting `model scenic.simulators.metadrive.model` still works here:
    _run_once() passes model="scenic.simulators.carla.model" to
    scenic.scenarioFromFile(), and that argument overrides the model
    statement in the file (Scenic's CompileOptions.modelOverride).
    """
    if src.suffix.lower() not in _CONVERTIBLE_SUFFIXES:
        return None

    try:
        convert_file, spec = resolve_converter()
    except Exception:
        log.error("Could not load converter; cannot convert %s:\n%s",
                  src, traceback.format_exc())
        return None

    try:
        written = convert_file(str(src), str(dest))
    except Exception:
        log.error("Conversion failed for %s (converter %s):\n%s",
                  src, spec, traceback.format_exc())
        return None

    if written is None:
        log.error("Converter %s returned None for %s (expected the written path).",
                  spec, src)
        return None

    return Path(written)


def _ensure_scenic_twins(input_dir: Path) -> None:
    """
    Ensures every convertible scenario file (see _CONVERTIBLE_SUFFIXES, e.g.
    .xosc) found recursively under input_dir has an up-to-date .scenic twin
    sitting right next to it (same name, .scenic extension), so
    run_directory()'s plain `rglob("*.scenic")` picks it up alongside any
    hand-written .scenic files.

    Nothing is moved or copied elsewhere: .scenic files and non-convertible
    files (map files such as .xodr/.snet, readmes, ...) are never touched,
    so whatever relative paths a scenario uses to reference them keep
    working unchanged. Twins are (re)written in place on every call, rather
    than skipped when already present, so a rerun always reflects the
    current converter's output instead of a stale one from a previous run.
    """
    input_dir = Path(input_dir)
    convertible_files = [
        p for p in input_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in _CONVERTIBLE_SUFFIXES
    ]
    if not convertible_files:
        return

    log.info(
        "Found %d convertible non-.scenic file(s) in %s; writing .scenic twins in place.",
        len(convertible_files), input_dir,
    )

    for src in convertible_files:
        dest = src.with_suffix(".scenic")
        converted_path = _convert_to_scenic(src, dest)
        if converted_path is None:
            log.warning("Could not convert %s - skipping.", src)


def _carla_town_from_scenic(scenic_path: Path) -> tuple[Optional[str], Optional[Path]]:
    text = scenic_path.read_text(encoding="utf-8")
    m = _MAP_PARAM_RE.search(text)
    if not m:
        return None, None
    xodr_path = (scenic_path.parent / m.group("path")).resolve()
    stem = xodr_path.stem

    if not xodr_path.exists():
        # Converted scenarios reference their .xodr relative to the source
        # scenario's own folder; if the map was never shipped alongside it,
        # say so here rather than failing deep inside CarlaSimulator.
        log.warning("Map %s referenced by %s does not exist.", xodr_path, scenic_path.name)

    if _CARLA_TOWN_RE.match(stem):
        return stem, xodr_path

    # Not a stock CARLA town (e.g. an SCTrans/LGSVL map coming out of the
    # .xosc converter). CarlaSimulator calls client.load_world(carla_map)
    # whenever carla_map is not None and raises RuntimeError if CARLA does
    # not know that name; passing None instead makes it ingest the .xodr via
    # generate_opendrive_world().
    log.info("'%s' is not a stock CARLA town; loading %s as OpenDRIVE.", stem, xodr_path.name)
    return None, xodr_path


class ScenicCarlaRunner:
    def __init__(
        self,
        output_dir: Path,
        tool_name: str = "ScenicRunner",
        engine: str = "behavior_agent",
        address: str = "127.0.0.1",
        port: int = 2000,
        timestep: float = 0.05,
        max_scenario_seconds: float = 120.0,
        client_timeout_s: float = 180.0,
    ):
        if engine not in ("behavior_agent", "autoware"):
            raise ValueError(f"Unknown engine: {engine}")

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.tool_name = tool_name
        self.engine = engine
        self.address = address
        self.port = port
        self.timestep = timestep
        self.max_scenario_seconds = max_scenario_seconds
        # Networking timeout for the CARLA client. Building a mesh from raw
        # OpenDRIVE is a single blocking call that can take minutes on a
        # large map, so this is far longer than a stock town would need.
        self.client_timeout_s = client_timeout_s

    # ------------------------------------------------------------------
    def run_file(self, scenic_path: Path, num_runs: int = 10) -> None:
        scenic_path = Path(scenic_path).resolve()
        scenario_id = scenic_path.stem
        text = scenic_path.read_text(encoding="utf-8")
        timeout_s = _default_timeout_s(text)
        max_steps = max(1, int(self.max_scenario_seconds / self.timestep))

        carla_map, xodr_path = _carla_town_from_scenic(scenic_path)

        with tempfile.TemporaryDirectory(prefix="safe_score_scenic_") as tmp_dir_str:
            tmp_scenic_path = _prepare_temp_scenic(scenic_path, Path(tmp_dir_str))

            for run_index in range(1, num_runs + 1):
                log.info("[%s] run %d/%d", scenario_id, run_index, num_runs)
                self._run_once(
                    tmp_scenic_path=tmp_scenic_path,
                    scenario_id=scenario_id,
                    run_index=run_index,
                    carla_map=carla_map,
                    xodr_path=xodr_path,
                    timeout_s=timeout_s,
                    max_steps=max_steps,
                )

    def _run_once(
        self,
        tmp_scenic_path: Path,
        scenario_id: str,
        run_index: int,
        carla_map: Optional[str],
        xodr_path: Optional[Path],
        timeout_s: float,
        max_steps: int,
    ) -> None:
        ctx = RunnerContext(
            world=None,  # filled in once the simulator connects
            client=None,
            tool=self.tool_name,
            generation_id=self.engine,
            scenario_id=scenario_id,
            run_index=run_index,
            output_dir=str(self.output_dir),
            delta_time=self.timestep,
            timeout_s=timeout_s,
        )

        sim = None
        try:
            scenario = scenic.scenarioFromFile(
                str(tmp_scenic_path),
                model="scenic.simulators.carla.model",
                mode2D=True,
                params={"_ss_ctx": ctx},
            )
            scene, _ = scenario.generate(maxIterations=2000)

            # Only raw-OpenDRIVE ingest needs the tuned mesh settings; a
            # stock town is loaded by name and never builds a mesh.
            mesh_ctx = (
                _tuned_opendrive_generation(OPENDRIVE_MESH_PARAMS)
                if carla_map is None
                else contextlib.nullcontext()
            )
            with mesh_ctx:
                sim = CarlaSimulator(
                    carla_map=carla_map,
                    map_path=xodr_path,
                    address=self.address,
                    port=self.port,
                    timeout=self.client_timeout_s,
                    render=False,
                    timestep=self.timestep,
                )
            ctx.world = sim.world
            ctx.client = sim.client

            t_start = time.time()
            simulation = sim.simulate(scene, maxSteps=max_steps)
            wall_time = time.time() - t_start

            if simulation is None:
                log.warning("[%s] run %d: simulation rejected by Scenic", scenario_id, run_index)
                return

            log.info("[%s] run %d completed in %.1fs", scenario_id, run_index, wall_time)
        except Exception:
            log.error("[%s] run %d failed:\n%s", scenario_id, run_index, traceback.format_exc())
        finally:
            if ctx.logger is not None and not ctx.logger.ended:
                filename = f"{scenario_id}_run_{run_index:02d}_log_basic.json"
                try:
                    ctx.logger.finalize_and_save(filename=filename)
                except Exception:
                    log.error(
                        "[%s] run %d: error in finalize_and_save:\n%s",
                        scenario_id, run_index, traceback.format_exc(),
                    )
            ctx.destroy_sensors()
            if sim is not None:
                try:
                    sim.destroy()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    def run_directory(self, input_dir: Path, num_runs: int = 10) -> None:
        input_dir = Path(input_dir)
        _ensure_scenic_twins(input_dir)
        scenic_files = sorted(input_dir.rglob("*.scenic"))
        if not scenic_files:
            raise FileNotFoundError(f"No .scenic file found in {input_dir}")

        log.info("Found %d .scenic file(s) in %s", len(scenic_files), input_dir)
        for i, scenic_path in enumerate(scenic_files, start=1):
            log.info("===== [%d/%d] %s =====", i, len(scenic_files), scenic_path.name)
            try:
                self.run_file(scenic_path, num_runs=num_runs)
            except Exception:
                log.error(
                    "Scenario %s failed entirely:\n%s",
                    scenic_path.name, traceback.format_exc(),
                )
