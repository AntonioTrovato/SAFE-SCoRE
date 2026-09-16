"""
run_pipeline.py

Entry point for the SOTIF evaluation pipeline, run on base logs that already
exist under outputs/. Takes them through enrichment, ODD scoring, hazard and
residual-risk computation, the final report and ODD/TC coverage. See
src/pipeline/sotif_pipeline.py for the step-by-step description.

Usage (from the repository root):

    # one result folder - the normal case
    python -m src.pipeline.run_pipeline --output_folder my_run

    # every folder under outputs/
    python -m src.pipeline.run_pipeline --all
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
for _p in (REPO_ROOT, SRC_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from pipeline.sotif_pipeline import SOTIFPipeline


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the SOTIF evaluation over existing base logs."
    )
    parser.add_argument(
        "--output_folder",
        help="Name of the folder under outputs/ to evaluate, e.g. 'my_run'.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Evaluate every folder under outputs/ instead of a single one.",
    )
    args = parser.parse_args()

    if bool(args.output_folder) == bool(args.all):
        parser.error("give exactly one of --output_folder or --all")

    SOTIFPipeline(REPO_ROOT, dataset=args.output_folder).run()


if __name__ == "__main__":
    main()
