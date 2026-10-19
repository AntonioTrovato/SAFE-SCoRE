import csv
import json
import math
import re
import argparse
from pathlib import Path
from typing import Dict, Any, List

from sotif_config import load_config, get_hazard_severities

# ---------------------------------------------------------------------
# Filename parsing: <scenario_id>_run_<NN>_log_basic.json
# ---------------------------------------------------------------------
def parse_filename(path: Path):
    regex = r"(.+)_run_(\d+)_log_basic\.json"
    m = re.match(regex, path.name)
    if not m:
        return None, None
    return m.group(1), int(m.group(2))


# ---------------------------------------------------------------------
# Extracts event_counts from a base log
# ---------------------------------------------------------------------
def extract_event_counts(data: Dict[str, Any], hazards: List[str]) -> Dict[str, int]:
    results = data.get("results", {})
    counts = results.get("event_counts", {})
    return {h: int(counts.get(h, 0)) for h in hazards}


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
    parser = argparse.ArgumentParser(description="Compute final SOTIF report")
    parser.add_argument(
        "--dataset_dir",
        required=True,
        help="Path to the dataset folder to analyze"
    )
    parser.add_argument("--config", default=None, help="Path to config/sotif_odd_tc.yaml (default: repo config)")
    args = parser.parse_args()

    # hazards and their severities are declared in the config (hazards.severity)
    SEVERITY_WEIGHTS = get_hazard_severities(load_config(Path(args.config) if args.config else None))
    HAZARDS = list(SEVERITY_WEIGHTS)

    dataset_dir = Path(args.dataset_dir).resolve()
    if not dataset_dir.exists():
        raise FileNotFoundError(f"Dataset dir not found: {dataset_dir}")

    dataset_name = dataset_dir.name

    # input: enriched / hazard / odd logs (depends on how you use this script)
    logs_dir = dataset_dir

    # output: final CSV, written inside the dataset's own output folder
    out_csv = dataset_dir / "SOTIF_Final.csv"

    print(f"[INFO] Dataset        : {dataset_name}")
    print(f"[INFO] Input logs     : {logs_dir}")
    print(f"[INFO] Output CSV     : {out_csv}")

    files = sorted(logs_dir.glob("*_run_*_log_basic.json"))
    if not files:
        raise SystemExit(f"No *_log_basic.json found in {logs_dir}")

    # -----------------------------------------------------------------
    # Group by scenario
    # -----------------------------------------------------------------
    grouped: Dict[str, list[Path]] = {}
    for f in files:
        sid, rid = parse_filename(f)
        if sid is None:
            print(f"[WARN] Unrecognized filename: {f.name}")
            continue
        grouped.setdefault(sid, []).append(f)

    # -----------------------------------------------------------------
    # CSV header
    # -----------------------------------------------------------------
    header = ["scenario_id", "num_runs"]

    # hazard rate per event
    for h in HAZARDS:
        header.append(f"HR_{h}")

    # risk per event
    for h in HAZARDS:
        header.append(f"R_{h}")

    # severity (constant)
    for h in HAZARDS:
        header.append(f"S_{h}")

    # scenario-level aggregates
    header.extend([
        "HR_avg",
        "R_avg",
        *EXEC_TIME_STATS,
    ])

    rows = []

    # -----------------------------------------------------------------
    # For each scenario
    # -----------------------------------------------------------------
    for scenario_id, runs in grouped.items():
        N = len(runs)

        hazard_run_counts = {h: 0 for h in HAZARDS}
        exec_times = []

        for p in runs:
            with p.open() as f:
                data = json.load(f)

            # hazard occurrence (at least once in the run)
            counts = extract_event_counts(data, HAZARDS)
            for h in HAZARDS:
                if counts[h] > 0:
                    hazard_run_counts[h] += 1

            # actual (wall-clock) execution time
            results = data.get("results", {})
            # a run without a valid execution time is left out of the time
            # statistics (it still counts for hazards)
            try:
                t = float(results.get("total_simulation_time"))
            except (TypeError, ValueError):
                t = float("nan")
            if math.isfinite(t):
                exec_times.append(t)
            else:
                print(f"[WARN] {p.name}: no valid total_simulation_time, excluded from execution-time statistics")

        # hazard rate
        HR = {h: hazard_run_counts[h] / N for h in HAZARDS}

        # severity
        S = {h: SEVERITY_WEIGHTS[h] for h in HAZARDS}

        # risk per event
        R = {h: HR[h] * S[h] for h in HAZARDS}

        # aggregates
        HR_avg = sum(HR[h] for h in HAZARDS) / len(HAZARDS)
        R_avg = sum(R[h] for h in HAZARDS) / len(HAZARDS)

        row = [scenario_id, N]

        for h in HAZARDS:
            row.append(round(HR[h], 4))

        for h in HAZARDS:
            row.append(round(R[h], 4))

        for h in HAZARDS:
            row.append(S[h])

        row.append(round(HR_avg, 4))
        row.append(round(R_avg, 4))
        row.extend(exec_time_stats(exec_times))

        rows.append(row)

    # -----------------------------------------------------------------
    # Write the final CSV
    # -----------------------------------------------------------------
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as fw:
        writer = csv.writer(fw)
        writer.writerow(header)
        for r in rows:
            writer.writerow(r)

    print(f"[OK] Final SOTIF report written to: {out_csv}")
    print(f"[OK] Scenarios processed: {len(rows)}")


if __name__ == "__main__":
    main()
