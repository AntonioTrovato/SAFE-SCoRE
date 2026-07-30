import json
import csv
import re
import argparse
from pathlib import Path
from typing import Dict, Any, List

from sotif_config import load_config, get_acceptance_threshold

# ---------------------------------------------------------------------
# Hazards considered (consistent with the base logs)
# ---------------------------------------------------------------------
HAZARDS = [
    "collision_vehicle",
    "collision_pedestrian",
    "collision_static",
    "red_light",
    "stop_sign",
    "off_road",
    "lane_invasion",
]

# ---------------------------------------------------------------------
# Severity weights (CARLA Leaderboard + minimal extension)
# ---------------------------------------------------------------------
SEVERITY_WEIGHTS = {
    # CARLA Leaderboard
    "collision_pedestrian": 0.50,
    "collision_vehicle": 0.60,
    "collision_static": 0.65,
    "red_light": 0.70,
    "stop_sign": 0.80,

    # Minimal, motivated extension
    "off_road": 0.90,
    "lane_invasion": 0.85,
}

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
def extract_hazard_counts(data: Dict[str, Any]) -> Dict[str, int]:
    results = data.get("results", {})
    counts = results.get("event_counts", {})
    return {h: int(counts.get(h, 0)) for h in HAZARDS}


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

    acceptance_threshold = get_acceptance_threshold(load_config(Path(args.config) if args.config else None))

    # project root
    base_dir = dataset_dir.parents[1]

    dataset_name = dataset_dir.name

    # input logs (or intermediate files) inside the dataset folder
    logs_dir = dataset_dir

    # dynamic output CSV (one per dataset)
    out_csv = base_dir / "datasets" / f"{dataset_name}_sotif_hazard_leaderboard.csv"

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
    header.append("acceptance_threshold")
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

            counts = extract_hazard_counts(data)

            for h in HAZARDS:
                if counts[h] > 0:
                    hazard_run_counts[h] += 1

        # empirical probability
        P = {h: hazard_run_counts[h] / N for h in HAZARDS}

        # fixed severity (Leaderboard)
        S = {h: SEVERITY_WEIGHTS[h] for h in HAZARDS}

        # residual risk
        R = {h: P[h] * S[h] for h in HAZARDS}

        is_non_acceptable = any(R[h] > acceptance_threshold for h in HAZARDS)

        row = [scenario_id, N]
        for h in HAZARDS:
            row.append(round(P[h], 4))
        for h in HAZARDS:
            row.append(S[h])
        for h in HAZARDS:
            row.append(round(R[h], 4))
        row.append(acceptance_threshold)
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
