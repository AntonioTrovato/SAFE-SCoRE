"""
scenic_carla_runner.py

Executes .scenic scenario files on CARLA and produces base-log JSON files
compatible with the rest of the SAFE-SCoRE pipeline (same shape as the logs
produced by external tools via data_gathering.carlaBasicLogger, see
docs/integration.md / docs/base_log_json.md).

Default engine ("behavior_agent") drives the ego with Scenic's own compiled
`behavior` (e.g. `EgoBehavior()` in the sample .scenic files) via
scenic.simulators.carla - no separate CARLA BehaviorAgent needed. This path
is unchanged and unaffected by anything below.

The "autoware" engine instead hands the ego to a running Autoware stack, by
splitting ownership of the simulation (see runner/autoware_session.py):

    Autoware  owns the map, the ego, its sensors and its control
    Scenic    owns the clock and every other actor, including their behaviours
    this file decides where the ego starts and where it is going, and logs

It requires Autoware to already be running against the same CARLA server and
the same map, launched with CARLA_EXTERNAL_TICK=1 so that it does not also
advance the world - two tick masters in synchronous mode double-step frames
and desync sensor data.
"""

from __future__ import annotations

import contextlib
import importlib
import logging
import multiprocessing
import os
import random
import re
import shlex
import subprocess
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

from runner.recorder import (  # noqa: E402
    GoalReached,
    RunnerContext,
    RunWallClockTimeout,
    on_monitor_step,
)

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
    # wall_height/additional_width: taller side barriers / wider drivable
    # margin than CARLA's defaults (1.0 / 0.6), tuned to stop vehicles
    # falling through gaps in the generated mesh. Overridable via env vars
    # because the right value appears to be CARLA-version-sensitive: under
    # 0.9.15 these were seen to intrude on a spawn point that was clear
    # under 0.9.16, rejecting the spawn (see SimulationCreationError:
    # "Unable to spawn object"). Lower them if you hit that; raise them if
    # actors fall through the road again.
    "wall_height": float(os.environ.get("SAFE_SCORE_OPENDRIVE_WALL_HEIGHT", 3.0)),
    "additional_width": float(os.environ.get("SAFE_SCORE_OPENDRIVE_ADDITIONAL_WIDTH", 3.0)),
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


@contextlib.contextmanager
def _spawn_diagnostics(scenario_id: str, run_index: int):
    """Temporarily wraps World.try_spawn_actor to log each spawn attempt's
    blueprint and location plus whether CARLA accepted it, so a bare
    "Unable to spawn object" from Scenic (which doesn't say which of
    several objects failed) can be traced back to the actual rejected
    blueprint/transform.
    """
    original = carla.World.try_spawn_actor

    def _patched(world, blueprint, transform, *args, **kwargs):
        actor = original(world, blueprint, transform, *args, **kwargs)
        loc = transform.location
        status = "spawned" if actor is not None else "REJECTED"
        log.info(
            "[%s] run %d: spawn %s at (%.2f, %.2f, %.2f) -> %s",
            scenario_id, run_index, blueprint.id, loc.x, loc.y, loc.z, status,
        )
        return actor

    carla.World.try_spawn_actor = _patched
    try:
        yield
    finally:
        carla.World.try_spawn_actor = original

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
DEFAULT_XOSC_CONVERTER = "converter.AUTOWARE_converter:convert_file"


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


def _run_once_worker(
    tmp_scenic_path: str,
    scenario_id: str,
    run_index: int,
    carla_map: Optional[str],
    xodr_path: Optional[str],
    timeout_s: float,
    max_steps: int,
    *,
    tool_name: str,
    engine: str,
    address: str,
    port: int,
    timestep: float,
    output_dir: str,
    client_timeout_s: float,
    max_wall_seconds: float,
    ego_speed_default: float = 11.11,
    attempt: int = 1,
) -> None:
    """Runs one scenario execution to completion. Module-level (not a
    method) and only plain/picklable arguments, so it can be launched as a
    multiprocessing.Process target by ScenicCarlaRunner._run_once - see
    that method's docstring for why a subprocess is needed here at all.
    """
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    ctx = RunnerContext(
        world=None,  # filled in once the simulator connects
        client=None,
        tool=tool_name,
        generation_id=engine,
        scenario_id=scenario_id,
        run_index=run_index,
        output_dir=output_dir,
        delta_time=timestep,
        timeout_s=timeout_s,
        wall_timeout_s=max_wall_seconds,
    )

    sim = None
    session = None
    try:
        scenario = scenic.scenarioFromFile(
            tmp_scenic_path,
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

        with contextlib.ExitStack() as stack:
            if engine == "autoware":
                from runner.autoware_session import (
                    AutowareSession,
                    autoware_ownership,
                    scene_ego_speed,
                    scene_ego_transform,
                )

                # Seeded per (scenario, run, attempt). The attempt matters:
                # scenarios that do not sample the ego pose (e.g. an ego at a
                # fixed OrientedPoint) produce an identical scene every time,
                # so without it a retry would reuse the same goal too and
                # reproduce the same failure exactly.
                session = AutowareSession(
                    time_limit_s=timeout_s,
                    step_period_s=timestep,
                    rng=random.Random(f"{scenario_id}:{run_index}:{attempt}"),
                )
                # The patches must be live before CarlaSimulator is built, or
                # its constructor reloads the map and destroys Autoware's ego.
                stack.enter_context(autoware_ownership(session))

            with mesh_ctx:
                sim = CarlaSimulator(
                    carla_map=carla_map,
                    map_path=xodr_path,
                    address=address,
                    port=port,
                    timeout=client_timeout_s,
                    render=False,
                    timestep=timestep,
                )
            ctx.world = sim.world
            ctx.client = sim.client
            log.info(
                "[%s] run %d: connected to map '%s'",
                scenario_id, run_index, ctx.world.get_map().name,
            )

            if session is not None:
                session.attach(sim.world)
                # Autoware needs the world ticking to finish spawning its ego,
                # converge localization and plan a route - but Scenic does not
                # start ticking until simulate() below. Pump ticks across that
                # gap, and stop before simulate() so there is only ever one
                # tick master.
                from runner.autoware_session import TickPump

                with TickPump(sim.world):
                    session.prepare(
                        scene_ego_transform(scene, sim.world),
                        target_speed=scene_ego_speed(scene) or ego_speed_default,
                    )
                ctx.autoware_session = session

            t_start = time.time()
            ctx.run_started_at = t_start
            with _spawn_diagnostics(scenario_id, run_index):
                simulation = sim.simulate(scene, maxSteps=max_steps)
            wall_time = time.time() - t_start

            if simulation is None:
                log.warning(
                    "[%s] run %d: simulation rejected by Scenic", scenario_id, run_index
                )
                return

            log.info("[%s] run %d completed in %.1fs", scenario_id, run_index, wall_time)
    except GoalReached as exc:
        log.info("[%s] run %d finished early: %s", scenario_id, run_index, exc)
    except RunWallClockTimeout as exc:
        log.warning("[%s] run %d aborted: %s", scenario_id, run_index, exc)
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
        if session is not None:
            session.finish()
            # Scenic's CarlaSimulator.destroy() drops the world out of
            # synchronous mode. autoware_ownership patches that out, but
            # sim.destroy() below runs in this finally block - outside the
            # patch's scope - so the original runs and leaves the world
            # free-running. Autoware then has no coherent clock, publishes no
            # trajectory, and every later run fails to engage while looking
            # perfectly healthy. Restore the settings explicitly afterwards.
            _restore_sync = (ctx.world, timestep)
        else:
            _restore_sync = None
        if sim is not None:
            try:
                sim.destroy()
            except Exception:
                pass
        if _restore_sync is not None and _restore_sync[0] is not None:
            world, step = _restore_sync
            try:
                s = world.get_settings()
                s.synchronous_mode = True
                s.fixed_delta_seconds = step
                world.apply_settings(s)
                log.info("[autoware] synchronous mode restored for the next run")
            except Exception:
                log.warning("[autoware] could not restore synchronous mode", exc_info=True)


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
        max_wall_seconds: float = 300.0,
        ego_speed_default: float = 11.11,
        carla_exe: Optional[str] = None,
        carla_launch_args: str = "",
        carla_boot_timeout_s: float = 90.0,
        autoware_map_path: Optional[str] = None,
        wsl_distro: str = "Ubuntu-22.04",
        max_run_attempts: int = 5,
        allow_wsl_shutdown: bool = True,
        follow_camera: str = "",
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
        # Real-world backstop, independent of max_scenario_seconds. maxSteps
        # only bounds *simulated* time and assumes each tick costs roughly
        # one real-time timestep; a stalled pileup can make ticks take much
        # longer than that in wall-clock terms, so the step cap alone never
        # ends the run. Checked once per simulated step (see on_monitor_step).
        self.max_wall_seconds = max_wall_seconds
        # Speed Autoware plans up to when a scenario does not declare one.
        # Autoware's own default is 4.17 m/s (15 km/h), well below what the
        # Scenic suites assume, which would leave the ego unable to keep up
        # with its own traffic.
        self.ego_speed_default = ego_speed_default
        # Auto-recovery from a crashed CARLA server (e.g. the UE4 landscape
        # LOD-thread access violation seen on repeated world reloads - not
        # fixable from here, it's an engine bug). carla_exe is the path to
        # CarlaUE4.exe; without it a dead server is a fatal error, same as
        # before, since there is nothing to relaunch.
        self.carla_exe = Path(carla_exe).resolve() if carla_exe else None
        self.carla_launch_args = shlex.split(carla_launch_args) if carla_launch_args else []
        self.carla_boot_timeout_s = carla_boot_timeout_s
        self._carla_proc: Optional[subprocess.Popen] = None
        # --engine autoware: a CARLA crash also strands Autoware (its
        # client hangs retrying a dead connection), so recovery has to be
        # paired. Without a map path we cannot relaunch it, and a crash
        # stays fatal - same contract as carla_exe.
        self.autoware_map_path = autoware_map_path
        self.wsl_distro = wsl_distro
        self._autoware_proc: Optional[subprocess.Popen] = None
        # How many times one run index is retried (restarting the whole
        # environment between attempts) before the scenario is abandoned.
        self.max_run_attempts = max_run_attempts
        # Stale DDS registrations survive a process restart, so the only
        # cure is restarting the WSL VM - which closes every WSL terminal.
        self.allow_wsl_shutdown = allow_wsl_shutdown
        # Outcome bookkeeping, reported by summarize().
        self.completed: list = []
        self.discarded: list = []
        # Optional spectator camera that chases the ego so a run can be
        # watched. Observer only - it never ticks - and only visible if
        # CARLA was started WITHOUT -RenderOffScreen.
        self._follower = None
        if follow_camera:
            from runner.follow_camera import SpectatorFollower
            self._follower = SpectatorFollower(
                address=self.address, port=self.port, mode=follow_camera
            )
            self._follower.start()

    # ------------------------------------------------------------------
    def _carla_alive(self, timeout_s: float = 5.0) -> bool:
        """Cheap liveness probe - a fresh short-timeout client asking for
        the server version, independent of self.client_timeout_s (which is
        tuned for slow map generation, not fast failure detection)."""
        try:
            probe = carla.Client(self.address, self.port)
            probe.set_timeout(timeout_s)
            probe.get_server_version()
            return True
        except Exception:
            return False


    def _autoware_alive(self) -> bool:
        """Whether Autoware can still answer on the routing API.

        Checked per run, not just at startup: its mission_planner container has
        been seen to segfault mid-suite, after which set_route_points simply
        never answers and every remaining run burns its full timeout budget
        before failing. Catching it here turns that into one restart.
        """
        from runner.autoware_control import AutowareController

        return AutowareController(distro=self.wsl_distro).nodes_alive(
            "/planning/mission_planning/mission_planner",
            "/control/trajectory_follower/controller_node_exe",
        )

    # ------------------------------------------------------------------
    def _clock_healthy(self, drain_ticks: int = 120) -> bool:
        """Drain any queued frames, then require one tick == one timestep.

        The single most valuable check in this mode: a world whose clock jumps
        gives Autoware incoherent sensor timing, so it never publishes a usable
        trajectory, so autonomous mode never becomes available and the ego
        never moves. Everything else is downstream of this.

        The drain matters. During Autoware's startup both the tick pump and the
        bridge's own startup ticks queue work faster than the server retires
        it, so the first tick afterwards flushes the whole backlog - one tick
        was measured advancing 9501 frames and 35 seconds. That is a queue to
        be emptied, not a broken clock, so tick until two consecutive ticks
        each advance exactly one frame and one timestep.

        Note: sampling get_snapshot().frame *without* ticking returns a cached
        value, so a free-running world can look frozen. Only tick deltas count.
        """
        try:
            probe = carla.Client(self.address, self.port)
            probe.set_timeout(60.0)
            world = probe.get_world()
            settings = world.get_settings()
            if not settings.synchronous_mode or not settings.fixed_delta_seconds:
                log.warning(
                    "clock check: world is not synchronous (sync=%s delta=%s)",
                    settings.synchronous_mode, settings.fixed_delta_seconds,
                )
                return False

            good = 0
            worst = None
            for _ in range(drain_ticks):
                before = world.get_snapshot().timestamp
                world.tick()
                after = world.get_snapshot().timestamp
                d_frame = after.frame - before.frame
                d_time = after.elapsed_seconds - before.elapsed_seconds
                if d_frame == 1 and abs(d_time - self.timestep) <= 0.002:
                    good += 1
                    if good >= 2:
                        return True
                else:
                    good = 0
                    worst = (d_frame, d_time)

            log.warning(
                "clock check: still unsettled after %d ticks (last bad tick: "
                "frame +%s, time +%.4f; expected +1, +%.2f)",
                drain_ticks, worst[0] if worst else "?",
                worst[1] if worst else float("nan"), self.timestep,
            )
            return False
        except RuntimeError as exc:
            log.warning("clock check: could not reach CARLA (%s)", exc)
            return False

    def _duplicate_node_count(self) -> int:
        """Stale DDS registrations left by hard-killed Autoware instances."""
        res = subprocess.run(
            ["wsl.exe", "-d", self.wsl_distro, "-e", "bash", "-lic",
             "source /opt/ros/humble/setup.bash >/dev/null 2>&1; "
             "source $HOME/autoware/install/setup.bash >/dev/null 2>&1; "
             "export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp; "
             "export CYCLONEDDS_URI=file://$HOME/cyclonedds.xml; "
             "t=$(timeout 15 ros2 node list 2>/dev/null | wc -l); "
             "u=$(timeout 15 ros2 node list 2>/dev/null | sort -u | wc -l); echo $((t-u))"],
            capture_output=True, text=True,
        ).stdout.strip().split()
        try:
            return int(res[-1]) if res else -1
        except ValueError:
            return -1

    def environment_healthy(self, verbose: bool = True) -> bool:
        """The pre-flight: everything that must hold before a run can succeed."""
        if not self._carla_alive():
            if verbose:
                log.warning("pre-flight: CARLA is not reachable")
            return False
        if self.engine != "autoware":
            return True
        if not self._autoware_alive():
            if verbose:
                log.warning("pre-flight: Autoware nodes are missing")
            return False
        dupes = self._duplicate_node_count()
        if dupes > 0:
            if verbose:
                log.warning("pre-flight: %d duplicate ROS nodes (stale DDS state)", dupes)
            return False
        if not self._clock_healthy():
            if verbose:
                log.warning("pre-flight: clock invariant failed (world is not truly synchronous)")
            return False
        if verbose:
            log.info("pre-flight OK: CARLA up, Autoware up, no duplicates, clock sane")
        return True

    @staticmethod
    def _carla_process_count() -> int:
        out = subprocess.run(["tasklist"], capture_output=True, text=True, errors="ignore").stdout
        return sum(1 for line in out.splitlines() if "CarlaUE4-Win64" in line)

    def _kill_all_carla(self, timeout_s: float = 30.0) -> None:
        """Kill every CARLA process and wait until none remain.

        The waiting is the point. `CarlaUE4.exe` is only a launcher: it spawns
        `CarlaUE4-Win64-Shipping.exe` and exits immediately, so the handle we
        hold is already dead and tells us nothing about the real server.
        Launching a replacement before the old one has gone leaves *two*
        servers bound to port 2000 - Autoware then talks to one and the runner
        to the other, and nothing works while everything looks alive.
        """
        for image in ("CarlaUE4-Win64-Shipping.exe", "CarlaUE4.exe", "CrashReportClient.exe"):
            subprocess.run(["taskkill", "/IM", image, "/F"], capture_output=True)
        self._carla_proc = None

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self._carla_process_count() == 0:
                return
            time.sleep(1.0)
        log.warning("CARLA processes still present after %.0fs.", timeout_s)

    def _restart_carla_server(self) -> None:
        if self.carla_exe is None:
            raise RuntimeError(
                f"CARLA at {self.address}:{self.port} is unreachable and no --carla_exe "
                "was configured, so it cannot be restarted automatically."
            )

        log.warning("Restarting CARLA (%s)...", self.carla_exe)
        self._kill_all_carla()

        self._carla_proc = subprocess.Popen([str(self.carla_exe), *self.carla_launch_args])

        deadline = time.time() + self.carla_boot_timeout_s
        while time.time() < deadline:
            if self._carla_alive():
                count = self._carla_process_count()
                if count > 1:
                    log.error("%d CARLA servers are running; killing and retrying.", count)
                    self._kill_all_carla()
                    self._carla_proc = subprocess.Popen(
                        [str(self.carla_exe), *self.carla_launch_args]
                    )
                    deadline = time.time() + self.carla_boot_timeout_s
                    continue
                log.info("CARLA is up.")
                return
            time.sleep(2.0)

        raise RuntimeError(
            f"CARLA did not come back up within {self.carla_boot_timeout_s:.0f}s."
        )

    def shutdown(self) -> None:
        if self._follower is not None:
            self._follower.stop()
            self._follower = None
        """Stops the CARLA server this runner itself launched via
        --carla_exe, if any. A no-op if CARLA was already running when the
        pipeline started (nothing to clean up) or --carla_exe wasn't set."""
        if self._autoware_proc is not None:
            self._kill_autoware()
        if self._carla_proc is None or self._carla_proc.poll() is not None:
            return
        log.info("Stopping CARLA server (%s)...", self.carla_exe)
        self._carla_proc.terminate()
        try:
            self._carla_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._carla_proc.kill()
            self._carla_proc.wait()
        self._carla_proc = None


    # ------------------------------------------------------------------
    def _kill_autoware(self, graceful_wait_s: float = 20.0) -> None:
        """Stop Autoware, preferring a clean shutdown over a kill.

        SIGINT first, and give it time. This matters more than it looks:
        hard-killed nodes stay advertised in CycloneDDS, the registrations
        accumulate across restarts, and once Autoware's duplicated_node_checker
        sees them it blocks autonomous mode permanently - while localization
        and routing still look green. A recovery policy that restarts often
        would poison its own environment within a few cycles.
        """
        if self._autoware_proc is not None and self._autoware_proc.poll() is None:
            self._autoware_proc.terminate()
            try:
                self._autoware_proc.wait(timeout=graceful_wait_s)
            except subprocess.TimeoutExpired:
                self._autoware_proc.kill()
        self._autoware_proc = None

        # SIGINT the launch itself, which propagates to every node.
        subprocess.run(
            ["wsl.exe", "-d", self.wsl_distro, "-e", "bash", "-lic",
             "pkill -INT -f 'ros2 launch autoware_launch' 2>/dev/null; true"],
            capture_output=True,
        )
        deadline = time.time() + graceful_wait_s
        while time.time() < deadline:
            left = subprocess.run(
                ["wsl.exe", "-d", self.wsl_distro, "-e", "bash", "-lic",
                 "pgrep -c -f component_container || echo 0"],
                capture_output=True, text=True,
            ).stdout.strip().split()
            if left and left[-1] == "0":
                log.info("Autoware shut down cleanly.")
                return
            time.sleep(2.0)

        log.warning("Autoware did not exit cleanly; forcing it (may leave stale DDS state).")
        subprocess.run(
            ["wsl.exe", "-d", self.wsl_distro, "-e", "bash", "-lic",
             "pkill -f 'ros2 launch autoware_launch'; pkill -f component_container; "
             "pkill -f autoware_carla_interface; pkill -f rviz2; sleep 3"],
            capture_output=True,
        )

    def _start_autoware(self, timeout_s: float = 300.0) -> bool:
        """Launch Autoware and wait until its bridge has really finished.

        "Ready" is not when the routing API answers - that happens well before
        the bridge has loaded the map into CARLA and spawned the ego, and a
        clock check at that point fails because synchronous mode has not been
        applied yet. Wait for the observable end state instead: the expected
        map is loaded, an ego exists, and one tick advances exactly one frame.

        Ticks are pumped throughout, because with CARLA_EXTERNAL_TICK=1 the
        bridge cannot finish its own startup unaided.
        """
        from runner.autoware_session import TickPump

        # Refuse to start a second stack; two bridges means two tick masters.
        leftover = subprocess.run(
            ["wsl.exe", "-d", self.wsl_distro, "-e", "bash", "-lic",
             "pgrep -c -f component_container || echo 0"],
            capture_output=True, text=True,
        ).stdout.strip().split()
        if leftover and leftover[-1] not in ("0", ""):
            self._kill_autoware()

        expected_map = str(self.autoware_map_path).rstrip("/").split("/")[-1]
        cmd = (
            "cd ~/autoware && source install/setup.bash && "
            "CARLA_EXTERNAL_TICK=1 ros2 launch autoware_launch e2e_simulator.launch.xml "
            f"map_path:={self.autoware_map_path} vehicle_model:=sample_vehicle "
            "sensor_model:=carla_sensor_kit simulator_type:=carla"
        )
        log.warning("Starting Autoware (map=%s)...", expected_map)
        self._autoware_proc = subprocess.Popen(
            ["wsl.exe", "-d", self.wsl_distro, "-e", "bash", "-lic", cmd],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        client = carla.Client(self.address, self.port)
        client.set_timeout(60.0)
        pump = TickPump(client.get_world(), rate_hz=20.0, client=client)
        pump.start()
        try:
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                try:
                    world = client.get_world()
                    on_map = world.get_map().name.split("/")[-1] == expected_map
                    has_ego = any(
                        a.attributes.get("role_name") == "ego_vehicle"
                        for a in world.get_actors().filter("vehicle.*")
                    )
                    if on_map and has_ego:
                        log.info("Autoware is up (map %s loaded, ego spawned).", expected_map)
                        return True
                except RuntimeError:
                    pass
                time.sleep(5.0)
        finally:
            pump.stop()

        log.error("Autoware did not finish starting within %.0fs.", timeout_s)
        return False

    def _recover(self, attempts: int = 3) -> bool:
        """Restart the whole environment until the pre-flight passes.

        Always restarts *both*, whichever side failed. Two reasons: a CARLA
        crash strands Autoware (its client retries a dead connection forever
        without raising), and restarting Autoware against a live CARLA makes it
        reload the map CARLA already has - the known UE4 access violation. A
        fresh CARLA comes up on its default map, so Autoware's load is a
        genuine change.
        """
        if self.engine == "autoware" and self.autoware_map_path is None:
            raise RuntimeError(
                "The environment needs restarting but no --autoware_map_path was "
                "given, so Autoware cannot be relaunched. Restart both by hand."
            )

        for attempt in range(1, attempts + 1):
            log.warning("Recovering the environment (attempt %d/%d)...", attempt, attempts)
            if self.engine == "autoware":
                self._kill_autoware()
            self._restart_carla_server()
            if self.engine == "autoware":
                self._start_autoware()

            if self.environment_healthy():
                log.info("Environment recovered.")
                return True

            # Duplicates survive a process restart - they live in DDS, not in
            # the processes - so the only cure is restarting the WSL VM.
            if self.engine == "autoware" and self._duplicate_node_count() > 0:
                if self.allow_wsl_shutdown:
                    log.warning(
                        "Stale DDS registrations persist; restarting the WSL VM "
                        "(this closes every WSL terminal)."
                    )
                    self._kill_autoware()
                    subprocess.run(["wsl.exe", "--shutdown"], capture_output=True)
                    time.sleep(10)
                else:
                    raise RuntimeError(
                        "Stale DDS registrations are blocking autonomous mode and "
                        "--allow_wsl_shutdown was not given. Run 'wsl --shutdown' "
                        "by hand and restart."
                    )

        log.error("Environment could not be recovered after %d attempts.", attempts)
        return False

    # ------------------------------------------------------------------
    def _map_matches_autoware(self, carla_map: Optional[str], scenario_id: str) -> bool:
        """Whether this scenario's map is the one Autoware is bound to.

        Autoware loads one map at launch and cannot change it, so a scenario on
        any other map is unrunnable in this engine. Checked here, before the
        worker starts, because inside the worker it is already too late: a
        non-stock map goes down Scenic's raw-OpenDRIVE path, which rebuilds the
        world under Autoware, hangs the run and takes CARLA down with it.

        Mismatches are skipped rather than fatal, so a mixed-map suite still
        runs whatever it can - the rest remains available via
        --engine behavior_agent.
        """
        try:
            probe = carla.Client(self.address, self.port)
            probe.set_timeout(10.0)
            current = probe.get_world().get_map().name.split("/")[-1]
        except RuntimeError:
            log.warning("could not read the running map; letting [%s] proceed", scenario_id)
            return True

        if carla_map is None:
            log.warning(
                "SKIPPING [%s]: it needs a non-stock map ingested as raw OpenDRIVE, "
                "which would rebuild the world Autoware is attached to. "
                "Autoware is running '%s'. Use --engine behavior_agent for this one.",
                scenario_id, current,
            )
            return False

        if carla_map.lower() != current.lower():
            log.warning(
                "SKIPPING [%s]: scenario map is '%s' but Autoware is running '%s'. "
                "Relaunch Autoware with the matching map_path, or use "
                "--engine behavior_agent.",
                scenario_id, carla_map, current,
            )
            return False
        return True

    def run_file(self, scenic_path: Path, num_runs: int = 10) -> None:
        scenic_path = Path(scenic_path).resolve()
        scenario_id = scenic_path.stem
        text = scenic_path.read_text(encoding="utf-8")
        timeout_s = _default_timeout_s(text)
        max_steps = max(1, int(self.max_scenario_seconds / self.timestep))

        carla_map, xodr_path = _carla_town_from_scenic(scenic_path)

        if self.engine == "autoware":
            # Bring the environment up (or back up) before anything else. The
            # map check has to come after, because it is Autoware that loads
            # the scenario's map into CARLA - checking first would compare
            # against whatever map a freshly booted CARLA happens to have.
            if not self.environment_healthy(verbose=False):
                if not self._recover():
                    self.discarded.append((scenario_id, 0, "environment unrecoverable"))
                    return

            if not self._map_matches_autoware(carla_map, scenario_id):
                # A stock-town mismatch normally means Autoware is bound to a
                # different map; recovering relaunches it against the map this
                # runner is configured for.
                if carla_map is not None and self.autoware_map_path is not None:
                    log.warning("Restarting so Autoware loads '%s'.", carla_map)
                    if not self._recover() or not self._map_matches_autoware(
                        carla_map, scenario_id
                    ):
                        self.discarded.append((scenario_id, 0, "map mismatch"))
                        return
                else:
                    self.discarded.append((scenario_id, 0, "map not runnable with Autoware"))
                    return

        with tempfile.TemporaryDirectory(prefix="safe_score_scenic_") as tmp_dir_str:
            tmp_scenic_path = _prepare_temp_scenic(scenic_path, Path(tmp_dir_str))

            for run_index in range(1, num_runs + 1):
                produced = False
                for attempt in range(1, self.max_run_attempts + 1):
                    log.info(
                        "[%s] run %d/%d (attempt %d/%d)",
                        scenario_id, run_index, num_runs, attempt, self.max_run_attempts,
                    )
                    if not self.environment_healthy(verbose=(attempt > 1)):
                        if not self._recover():
                            log.error(
                                "[%s] giving up: the environment cannot be recovered.",
                                scenario_id,
                            )
                            self.discarded.append((scenario_id, run_index, "environment unrecoverable"))
                            return

                    self._run_once(
                        tmp_scenic_path=tmp_scenic_path,
                        scenario_id=scenario_id,
                        run_index=run_index,
                        carla_map=carla_map,
                        xodr_path=xodr_path,
                        timeout_s=timeout_s,
                        max_steps=max_steps,
                        attempt=attempt,
                    )

                    # Success is simply "a log was written". A run where the ego
                    # barely moved is a legitimate result, not a failure; only a
                    # crash or a setup failure leaves no log behind.
                    expected = self.output_dir / f"{scenario_id}_run_{run_index:02d}_log_basic.json"
                    if expected.exists():
                        produced = True
                        break

                    log.warning(
                        "[%s] run %d produced no log (attempt %d/%d) - recovering.",
                        scenario_id, run_index, attempt, self.max_run_attempts,
                    )
                    if attempt < self.max_run_attempts and not self._recover():
                        break

                if not produced:
                    log.error(
                        "[%s] run %d failed %d times; discarding the whole scenario.",
                        scenario_id, run_index, self.max_run_attempts,
                    )
                    self.discarded.append(
                        (scenario_id, run_index, f"failed {self.max_run_attempts} attempts")
                    )
                    return

            self.completed.append((scenario_id, num_runs))

    def _run_once(
        self,
        tmp_scenic_path: Path,
        scenario_id: str,
        run_index: int,
        carla_map: Optional[str],
        xodr_path: Optional[Path],
        timeout_s: float,
        max_steps: int,
        attempt: int = 1,
    ) -> None:
        # Runs the actual scenario in its own OS process, so a hard
        # wall-clock timeout can always regain control by killing the
        # process, even if a native call inside it never returns (e.g.
        # CARLA's client stuck endlessly retrying a dead server connection
        # after a crash - no in-process check, including the tick-level
        # RunWallClockTimeout, can ever run in that case).
        proc = multiprocessing.Process(
            target=_run_once_worker,
            args=(
                str(tmp_scenic_path),
                scenario_id,
                run_index,
                carla_map,
                str(xodr_path) if xodr_path is not None else None,
                timeout_s,
                max_steps,
            ),
            kwargs=dict(
                tool_name=self.tool_name,
                engine=self.engine,
                address=self.address,
                port=self.port,
                timestep=self.timestep,
                output_dir=str(self.output_dir),
                client_timeout_s=self.client_timeout_s,
                max_wall_seconds=self.max_wall_seconds,
                ego_speed_default=self.ego_speed_default,
                attempt=attempt,
            ),
        )
        proc.start()
        # Grace beyond max_wall_seconds lets the worker's own internal
        # RunWallClockTimeout fire and exit cleanly (it saves the partial
        # log) when it can; this join timeout is only the backstop for when
        # it can't even get that far.
        proc.join(timeout=self.max_wall_seconds + 30.0)
        if proc.is_alive():
            log.warning(
                "[%s] run %d: worker unresponsive past its wall-clock cap "
                "(likely stuck in a native call, e.g. a dead CARLA connection "
                "retry loop) - killing it",
                scenario_id, run_index,
            )
            proc.terminate()
            proc.join(timeout=10)
            if proc.is_alive():
                proc.kill()
                proc.join()
        elif proc.exitcode != 0:
            log.warning(
                "[%s] run %d: worker process exited abnormally (code %s)",
                scenario_id, run_index, proc.exitcode,
            )

    # ------------------------------------------------------------------
    def summarize(self) -> None:
        """What actually completed, and what was abandoned and why."""
        log.info("=" * 62)
        log.info("SUITE SUMMARY")
        for scenario_id, n in self.completed:
            log.info("  COMPLETED  %-32s %d/%d runs", scenario_id, n, n)
        for scenario_id, run_index, why in self.discarded:
            log.info("  DISCARDED  %-32s at run %d (%s)", scenario_id, run_index, why)
        if not self.completed and not self.discarded:
            log.info("  (nothing ran)")
        log.info("=" * 62)

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
