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

import logging
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

import scenic  # noqa: E402
from scenic.simulators.carla import CarlaSimulator  # noqa: E402

from runner.recorder import RunnerContext, on_monitor_step  # noqa: E402

log = logging.getLogger("ScenicCarlaRunner")

_MAP_PARAM_RE = re.compile(
    r"param\s+map\s*=\s*localPath\((['\"])(?P<path>.*?)\1\)"
)

_MONITOR_SNIPPET = """

from runner.recorder import on_monitor_step

monitor SafeScoreRecorder():
    while True:
        on_monitor_step(globalParameters._ss_ctx, ego)
        wait

require monitor SafeScoreRecorder()
"""


def _default_timeout_s(scenic_text: str, fallback: float = 60.0) -> float:
    m = re.search(r"terminate\s+after\s+(\d+(?:\.\d+)?)\s+seconds", scenic_text)
    return float(m.group(1)) if m else fallback


def _prepare_temp_scenic(scenic_path: Path, tmp_dir: Path) -> Path:
    """Copy scenic_path into tmp_dir with its `param map = localPath(...)`
    rewritten to an absolute path (so the copy can live anywhere) and the
    recorder monitor appended."""
    text = scenic_path.read_text(encoding="utf-8")

    def _rewrite_map(match: re.Match) -> str:
        quote = match.group(1)
        rel = match.group("path")
        abs_path = (scenic_path.parent / rel).resolve()
        return f"param map = localPath({quote}{abs_path}{quote})"

    text = _MAP_PARAM_RE.sub(_rewrite_map, text, count=1)
    text += _MONITOR_SNIPPET

    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / scenic_path.name
    tmp_path.write_text(text, encoding="utf-8")
    return tmp_path


def _carla_town_from_scenic(scenic_path: Path) -> tuple[Optional[str], Optional[Path]]:
    text = scenic_path.read_text(encoding="utf-8")
    m = _MAP_PARAM_RE.search(text)
    if not m:
        return None, None
    xodr_path = (scenic_path.parent / m.group("path")).resolve()
    return xodr_path.stem, xodr_path


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
    ):
        if engine not in ("behavior_agent", "autoware"):
            raise ValueError(f"engine sconosciuto: {engine}")

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.tool_name = tool_name
        self.engine = engine
        self.address = address
        self.port = port
        self.timestep = timestep
        self.max_scenario_seconds = max_scenario_seconds

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

            sim = CarlaSimulator(
                carla_map=carla_map,
                map_path=xodr_path,
                address=self.address,
                port=self.port,
                timeout=20,
                render=False,
                timestep=self.timestep,
            )
            ctx.world = sim.world
            ctx.client = sim.client

            t_start = time.time()
            simulation = sim.simulate(scene, maxSteps=max_steps)
            wall_time = time.time() - t_start

            if simulation is None:
                log.warning("[%s] run %d: simulazione rifiutata da Scenic", scenario_id, run_index)
                return

            log.info("[%s] run %d completata in %.1fs", scenario_id, run_index, wall_time)
        except Exception:
            log.error("[%s] run %d fallita:\n%s", scenario_id, run_index, traceback.format_exc())
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
        scenic_files = sorted(input_dir.rglob("*.scenic"))
        if not scenic_files:
            raise FileNotFoundError(f"No .scenic file found in {input_dir}")

        log.info("Trovati %d file .scenic in %s", len(scenic_files), input_dir)
        for i, scenic_path in enumerate(scenic_files, start=1):
            log.info("===== [%d/%d] %s =====", i, len(scenic_files), scenic_path.name)
            try:
                self.run_file(scenic_path, num_runs=num_runs)
            except Exception:
                log.error(
                    "Scenario %s fallito interamente:\n%s",
                    scenic_path.name, traceback.format_exc(),
                )
