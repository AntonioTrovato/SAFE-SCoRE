import csv
import json
import re
import argparse
from pathlib import Path
from typing import Dict, Any

# ---------------------------------------------------------------------
# Hazards considered (aligned with the Leaderboard + the actual pipeline)
# ---------------------------------------------------------------------
HAZARDS = [
    "collision_pedestrian",
    "collision_vehicle",
    "collision_static",
    "red_light",
    "stop_sign",
    "off_road",
    "lane_invasion",
]

# ---------------------------------------------------------------------
# Standard severity weights (CARLA Leaderboard + minimal extension)
# ---------------------------------------------------------------------
SEVERITY_WEIGHTS = {
    "collision_pedestrian": 0.50,
    "collision_vehicle": 0.60,
    "collision_static": 0.65,
    "red_light": 0.70,
    "stop_sign": 0.80,
    "off_road": 0.90,
    "lane_invasion": 0.85,
}

# Threshold (meters) below which the route is considered completed
COMPLETION_THRESHOLD_M = 8.0


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
def extract_event_counts(data: Dict[str, Any]) -> Dict[str, int]:
    results = data.get("results", {})
    counts = results.get("event_counts", {})
    return {h: int(counts.get(h, 0)) for h in HAZARDS}


def _euclidean_2d(a, b) -> float:
    dx = float(a[0]) - float(b[0])
    dy = float(a[1]) - float(b[1])
    return (dx * dx + dy * dy) ** 0.5


def is_route_completed(log: Dict[str, Any], threshold_m: float = COMPLETION_THRESHOLD_M) -> bool:
    """
    Considers the route completed if the ego (last frame) is within threshold_m
    of mission.end_location (2D). If enriched functional metrics are present,
    prefer those (more robust and consistent with the rest of the pipeline).
    """
    # 1) if the log has already been enriched, use the already-computed robust evaluation
    try:
        perf = log.get("results", {}).get("functional_metrics", {}).get("performance", {})
        if isinstance(perf, dict):
            # field added by the updated pipeline
            if "is_completed_final" in perf and perf["is_completed_final"] is not None:
                return bool(perf["is_completed_final"])
            # fallback: completion_frame present
            if perf.get("completion_frame") not in (None, 0):
                return True
    except Exception:
        pass

    mission = log.get("mission", {})
    end_loc = mission.get("end_location")
    frames = log.get("frames", [])

    if not end_loc or len(end_loc) < 2:
        return False
    if not frames:
        return False

    last_frame = frames[-1]
    ego = last_frame.get("ego_vehicle", {})
    ego_loc = ego.get("location")

    if not ego_loc or len(ego_loc) < 2:
        return False

    dist = _euclidean_2d(ego_loc, end_loc)
    return dist <= threshold_m


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
    args = parser.parse_args()

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
        "T_exec_avg",
        "completed_runs",
        "completion_rate",
    ])

    rows = []

    # -----------------------------------------------------------------
    # For each scenario
    # -----------------------------------------------------------------
    for scenario_id, runs in grouped.items():
        N = len(runs)

        hazard_run_counts = {h: 0 for h in HAZARDS}
        runtime_sum = 0.0
        completed_runs = 0

        for p in runs:
            with p.open() as f:
                data = json.load(f)

            # hazard occurrence (at least once in the run)
            counts = extract_event_counts(data)
            for h in HAZARDS:
                if counts[h] > 0:
                    hazard_run_counts[h] += 1

            # actual (wall-clock) execution time
            results = data.get("results", {})
            runtime_sum += float(results.get("total_simulation_time", 0.0))

            # route completed?
            if is_route_completed(data, threshold_m=COMPLETION_THRESHOLD_M):
                completed_runs += 1

        # hazard rate
        HR = {h: hazard_run_counts[h] / N for h in HAZARDS}

        # severity
        S = {h: SEVERITY_WEIGHTS[h] for h in HAZARDS}

        # risk per event
        R = {h: HR[h] * S[h] for h in HAZARDS}

        # aggregates
        HR_avg = sum(HR[h] for h in HAZARDS) / len(HAZARDS)
        R_avg = sum(R[h] for h in HAZARDS) / len(HAZARDS)
        T_exec_avg = runtime_sum / N
        completion_rate = completed_runs / N

        row = [scenario_id, N]

        for h in HAZARDS:
            row.append(round(HR[h], 4))

        for h in HAZARDS:
            row.append(round(R[h], 4))

        for h in HAZARDS:
            row.append(S[h])

        row.append(round(HR_avg, 4))
        row.append(round(R_avg, 4))
        row.append(round(T_exec_avg, 4))
        row.append(completed_runs)
        row.append(round(completion_rate, 4))

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
