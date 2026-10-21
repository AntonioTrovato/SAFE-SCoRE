"""
compute_execution_time_report.py

Final execution-time report. For every scenario it writes the statistics
(mean, median, min, max, 90th/95th/99th percentile) of the wall-clock time of
its executions, read from results.total_wall_clock_time of each base log.

Hazard rates, residual risks and severities are not repeated here: they are
in final_hazard_report.csv (compute_sotif_hazard.py).
"""

import csv
import json
import math
import re
import argparse
from pathlib import Path
from typing import Dict


# ---------------------------------------------------------------------
# Filename parsing: <scenario_id>_run_<NN>_log_basic.json
# ---------------------------------------------------------------------
def parse_filename(path: Path):
    regex = r"(.+)_run_(\d+)_log_basic\.json"
    m = re.match(regex, path.name)
    if not m:
        return None, None
    return m.group(1), int(m.group(2))


# Statistics reported for the per-run execution time of each scenario.
EXEC_TIME_STATS = ["T_exec_avg", "T_exec_median", "T_exec_min", "T_exec_max",
                   "T_exec_p90", "T_exec_p95", "T_exec_p99"]


def _percentile(sorted_vals, q: float) -> float:
    """q-th percentile (0-100) of an ascending list, linear interpolation
    (same as numpy.percentile's default)."""
    k = (len(sorted_vals) - 1) * q / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def exec_time_stats(times) -> list:
    """[avg, median, min, max, p90, p95, p99] of the valid execution times,
    rounded; empty strings if there is none."""
    if not times:
        return [""] * len(EXEC_TIME_STATS)
    v = sorted(times)
    vals = [sum(v) / len(v), _percentile(v, 50), v[0], v[-1],
            _percentile(v, 90), _percentile(v, 95), _percentile(v, 99)]
    return [round(x, 4) for x in vals]


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Compute the final execution-time report")
    parser.add_argument(
        "--dataset_dir",
        required=True,
        help="Path to the dataset folder to analyze"
    )
    args = parser.parse_args()

    dataset_dir = Path(args.dataset_dir).resolve()
    if not dataset_dir.exists():
        raise FileNotFoundError(f"Dataset dir not found: {dataset_dir}")

    logs_dir = dataset_dir

    # output: written inside the dataset's own output folder
    out_csv = dataset_dir / "final_execution_time_report.csv"

    print(f"[INFO] Dataset        : {dataset_dir.name}")
    print(f"[INFO] Input logs     : {logs_dir}")
    print(f"[INFO] Output CSV     : {out_csv}")

    files = sorted(logs_dir.glob("*_run_*_log_basic.json"))
    if not files:
        raise SystemExit(f"No *_log_basic.json found in {logs_dir}")

    # Group by scenario
    grouped: Dict[str, list] = {}
    for f in files:
        sid, rid = parse_filename(f)
        if sid is None:
            print(f"[WARN] Unrecognized filename: {f.name}")
            continue
        grouped.setdefault(sid, []).append(f)

    header = ["scenario_id", *EXEC_TIME_STATS]
    rows = []

    for scenario_id, runs in grouped.items():
        exec_times = []
        for p in runs:
            with p.open() as f:
                data = json.load(f)

            # wall-clock execution time of the run. A run without a valid
            # one is left out of the statistics.
            results = data.get("results", {})
            try:
                # "total_simulation_time" is the name used by logs written
                # before the field was renamed; same quantity (wall-clock s)
                t = float(results.get("total_wall_clock_time", results.get("total_simulation_time")))
            except (TypeError, ValueError):
                t = float("nan")
            if math.isfinite(t):
                exec_times.append(t)
            else:
                print(f"[WARN] {p.name}: no valid total_wall_clock_time, excluded from execution-time statistics")

        rows.append([scenario_id, *exec_time_stats(exec_times)])

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as fw:
        writer = csv.writer(fw)
        writer.writerow(header)
        writer.writerows(rows)

    print(f"[OK] Final execution-time report written to: {out_csv}")
    print(f"[OK] Scenarios processed: {len(rows)}")


if __name__ == "__main__":
    main()
