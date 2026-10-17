import json
import csv
import re
import argparse
from pathlib import Path
from typing import Dict, Any, List

from sotif_config import load_config, get_acceptance_thresholds, get_hazard_severities

# ---------------------------------------------------------------------
# Filename parsing
# Expect: <scenario_id>_run_<NN>_log_basic.json
# ---------------------------------------------------------------------
def parse_filename(path: Path):
    regex = r"(.+)_run_(\d+)_log_basic\.json"
    m = re.match(regex, path.name)
    if not m:
        return None, None
    scenario_id = m.group(1)
    run_id = int(m.group(2))
    return scenario_id, run_id


# ---------------------------------------------------------------------
# Extracts the hazard counts from a base log
# ---------------------------------------------------------------------
def extract_hazard_counts(data: Dict[str, Any], hazards: List[str]) -> Dict[str, int]:
    results = data.get("results", {})
    counts = results.get("event_counts", {})
    return {h: int(counts.get(h, 0)) for h in hazards}


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Compute SOTIF Hazard (Leaderboard)")
    parser.add_argument(
        "--dataset_dir",
        required=True,
        help="Path to the dataset folder to analyze"
    )
    parser.add_argument("--config", default=None, help="Path to config/sotif_odd_tc.yaml (default: repo config)")
    args = parser.parse_args()

    dataset_dir = Path(args.dataset_dir).resolve()
    if not dataset_dir.exists():
        raise FileNotFoundError(f"Dataset dir not found: {dataset_dir}")

    config = load_config(Path(args.config) if args.config else None)
    # hazards and their severities are declared in the config (hazards.severity)
    SEVERITY_WEIGHTS = get_hazard_severities(config)
    HAZARDS = list(SEVERITY_WEIGHTS)
    # one acceptance threshold per hazard (hazards.acceptance_threshold)
    ACCEPTANCE_THRESHOLDS = get_acceptance_thresholds(config)

    dataset_name = dataset_dir.name

    # input logs (or intermediate files) inside the dataset folder
    logs_dir = dataset_dir

    # output CSV, written inside the dataset's own output folder
    out_csv = dataset_dir / "sotif_hazard_leaderboard.csv"

    print(f"[INFO] Dataset        : {dataset_name}")
    print(f"[INFO] Input logs     : {logs_dir}")
    print(f"[INFO] Output CSV     : {out_csv}")

    files = sorted(logs_dir.glob("*_run_*_log_basic.json"))
    if not files:
        raise SystemExit(f"No *_log_basic.json found in {logs_dir}")

    # group by scenario
    grouped: Dict[str, List[Path]] = {}
    for f in files:
        sid, rid = parse_filename(f)
        if sid is None:
            print(f"[WARN] Unrecognized filename: {f.name}")
            continue
        grouped.setdefault(sid, []).append(f)

    # CSV header
    header = [
        "scenario_id",
        "num_runs",
    ]

    for h in HAZARDS:
        header.append(f"P_{h}")
    for h in HAZARDS:
        header.append(f"S_{h}")
    for h in HAZARDS:
        header.append(f"R_{h}")
    for h in HAZARDS:
        header.append(f"acceptance_threshold_{h}")
    header.append("is_non_acceptable")

    rows = []

    # -----------------------------------------------------------------
    # For each scenario
    # -----------------------------------------------------------------
    for scenario_id, runs in grouped.items():
        N = len(runs)

        # count of runs in which the hazard occurs at least once
        hazard_run_counts = {h: 0 for h in HAZARDS}

        for p in runs:
            with p.open() as f:
                data = json.load(f)

            counts = extract_hazard_counts(data, HAZARDS)

            for h in HAZARDS:
                if counts[h] > 0:
                    hazard_run_counts[h] += 1

        # empirical probability
        P = {h: hazard_run_counts[h] / N for h in HAZARDS}

        # fixed severity (Leaderboard)
        S = {h: SEVERITY_WEIGHTS[h] for h in HAZARDS}

        # residual risk
        R = {h: P[h] * S[h] for h in HAZARDS}

        is_non_acceptable = any(R[h] > ACCEPTANCE_THRESHOLDS[h] for h in HAZARDS)

        row = [scenario_id, N]
        for h in HAZARDS:
            row.append(round(P[h], 4))
        for h in HAZARDS:
            row.append(S[h])
        for h in HAZARDS:
            row.append(round(R[h], 4))
        for h in HAZARDS:
            row.append(ACCEPTANCE_THRESHOLDS[h])
        row.append(is_non_acceptable)

        rows.append(row)

    # write the CSV
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="") as fw:
        writer = csv.writer(fw)
        writer.writerow(header)
        for r in rows:
            writer.writerow(r)

    print(f"[OK] SOTIF Hazard (Leaderboard) computed for {len(rows)} scenarios.")
    print(f"[OK] CSV written to: {out_csv}")


if __name__ == "__main__":
    main()
