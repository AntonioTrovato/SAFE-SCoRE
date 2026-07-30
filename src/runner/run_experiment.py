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
    )
    runner.run_directory(Path(args.input_dir), num_runs=args.num_runs)

    if not args.skip_enrichment:
        pipeline = SOTIFPipeline(REPO_ROOT)
        pipeline.run()


if __name__ == "__main__":
    main()
