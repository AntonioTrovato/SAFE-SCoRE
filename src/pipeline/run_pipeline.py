"""
run_pipeline.py

Entry point for the SOTIF enrichment pipeline: processes every dataset
folder under outputs/ (already-populated base logs) through the ODD,
hazard, final-report, and ODD/TC coverage steps. See src/pipeline/sotif_pipeline.py
for the step-by-step description.

Usage (from the repository root):
    python -m src.pipeline.run_pipeline
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
for _p in (REPO_ROOT, SRC_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from pipeline.sotif_pipeline import SOTIFPipeline

if __name__ == "__main__":
    pipeline = SOTIFPipeline(REPO_ROOT)
    pipeline.run()
