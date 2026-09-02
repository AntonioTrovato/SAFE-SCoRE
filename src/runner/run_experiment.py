"""
run_experiment.py

Single entry point for the stage-1 SOTIF tool: given a folder of .scenic
scenario files, executes each one on CARLA (10x by default) and then runs
the existing SOTIF enrichment/metrics pipeline on the resulting logs.

Usage (from the repository root):
    python -m src.runner.run_experiment --input_dir scenic_example/common --output_folder scenic_demo
    python -m src.runner.run_experiment --input_dir scenic_example --output_folder scenic_full \
        --num_runs 10 --engine autoware --address 10.0.0.5 --port 2000
"""

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
for _p in (REPO_ROOT, SRC_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from pipeline.sotif_pipeline import SOTIFPipeline
from runner.scenic_carla_runner import ScenicCarlaRunner


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a suite of .scenic scenarios on CARLA and compute SOTIF metrics."
    )
    parser.add_argument("--input_dir", required=True, help="Folder containing the .scenic files (searched recursively)")
    parser.add_argument(
        "--output_folder",
        required=True,
        help="Name of the output folder, under outputs/<output_folder>/",
    )
    parser.add_argument("--num_runs", type=int, default=10, help="Executions per scenario (default: 10)")
    parser.add_argument(
        "--engine",
        choices=["behavior_agent", "autoware"],
        default="behavior_agent",
        help="behavior_agent: ego driven by the behavior compiled into the Scenic file (default). "
        "autoware: connects instead to the remote CARLA+Autoware service (--address/--port).",
    )
    parser.add_argument("--address", default="127.0.0.1", help="CARLA server address")
    parser.add_argument("--port", type=int, default=2000, help="CARLA server port")
    parser.add_argument("--timestep", type=float, default=0.05, help="Simulation step size (s)")
    parser.add_argument(
        "--max_scenario_seconds",
        type=float,
        default=120.0,
        help="Safety cap per execution, independent of the scenario's own 'terminate after'",
    )
    parser.add_argument(
        "--client_timeout",
        type=float,
        default=180.0,
        help="CARLA client networking timeout (s). Raise it for large maps ingested "
        "as raw OpenDRIVE, where world generation is one long blocking call.",
    )
    parser.add_argument(
        "--max_wall_seconds",
        type=float,
        default=300.0,
        help="Real-world (wall-clock) cap per execution, checked once per simulated "
        "step. Unlike --max_scenario_seconds (which bounds simulated time via a step "
        "count), this catches runs where ticks themselves take far longer than real "
        "time, e.g. a stalled/gridlocked pileup, which would otherwise never end.",
    )
    parser.add_argument(
        "--carla_exe",
        default=None,
        help="Path to CarlaUE4.exe. If set, a crashed/unreachable CARLA server is "
        "detected before each run and restarted automatically, sacrificing only the "
        "run that was in flight when it died. Without it, a crashed server is fatal "
        "and must be restarted manually.",
    )
    parser.add_argument(
        "--carla_launch_args",
        default="",
        help="Extra command-line flags passed to CarlaUE4.exe on restart, as a single "
        "quoted string (e.g. \"-RenderOffScreen -quality-level=Low\"). Ignored if "
        "--carla_exe is not set.",
    )
    parser.add_argument(
        "--carla_boot_timeout",
        type=float,
        default=90.0,
        help="How long to wait (s) for a restarted CARLA server to accept connections "
        "before giving up.",
    )
    parser.add_argument(
        "--ego_speed_default",
        type=float,
        default=11.11,
        help="Speed (m/s) Autoware plans up to when a scenario declares none. "
        "A scenario that samples its own ego speed (e.g. EGO_SPEED) overrides this "
        "per run. Autoware's stock default is 4.17 m/s, slower than the NPCs in "
        "these suites.",
    )
    parser.add_argument(
        "--autoware_map_path",
        default=None,
        help="Autoware map directory inside WSL (e.g. $HOME/autoware/autoware_map/Town05). "
        "With --engine autoware this lets the runner relaunch Autoware when CARLA "
        "crashes; a CARLA crash strands Autoware, so recovery has to restart both. "
        "Without it, a crash is fatal and both must be restarted by hand.",
    )
    parser.add_argument(
        "--wsl_distro",
        default="Ubuntu-22.04",
        help="WSL distribution Autoware runs in",
    )
    parser.add_argument(
        "--skip_enrichment",
        action="store_true",
        help="Only execute the scenarios, without running the SOTIF enrichment pipeline afterwards",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    output_dir = REPO_ROOT / "outputs" / args.output_folder

    runner = ScenicCarlaRunner(
        output_dir=output_dir,
        tool_name=args.output_folder,
        engine=args.engine,
        address=args.address,
        port=args.port,
        timestep=args.timestep,
        max_scenario_seconds=args.max_scenario_seconds,
        client_timeout_s=args.client_timeout,
        max_wall_seconds=args.max_wall_seconds,
        carla_exe=args.carla_exe,
        carla_launch_args=args.carla_launch_args,
        carla_boot_timeout_s=args.carla_boot_timeout,
        ego_speed_default=args.ego_speed_default,
        autoware_map_path=args.autoware_map_path,
        wsl_distro=args.wsl_distro,
    )
    try:
        runner.run_directory(Path(args.input_dir), num_runs=args.num_runs)

        if not args.skip_enrichment:
            pipeline = SOTIFPipeline(REPO_ROOT)
            pipeline.run()
    finally:
        # Only stops CARLA if this run launched it itself via --carla_exe;
        # a no-op otherwise (e.g. a server you started and want to keep).
        runner.shutdown()


if __name__ == "__main__":
    main()
