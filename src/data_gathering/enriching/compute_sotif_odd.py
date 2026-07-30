"""
compute_sotif_odd.py

Computes descriptive ODD (Operational Design Domain) sub-scores and
triggering conditions for each base log in a dataset, driven entirely by
config/sotif_odd_tc.yaml (see that file for the factor/condition schema)
instead of hardcoded Python dicts - so a user can plug in their own SUT's
ODD/TC taxonomy without touching this file.

Reads, per log:
  - log["world_state"]: raw facts snapshotted by the runner at scenario
    start (weather floats, actor counts, map name, speed limit, mission
    timeout/waypoints) - see runner/recorder.py's snapshot_world_state().
  - log["results"]["critical_metrics"] / ["dynamics_metrics"]: already
    computed by data_gathering/enriching/orchestrator.py.

Writes, per log:
  - log["sotif"]["odd_env"/"odd_infra"/"odd_traffic"/"odd_operational"/"odd_global"]
  - log["sotif"]["odd_values"]: the categorical value each configured ODD
    factor took (used by analysis/odd_tc_coverage.py to compute coverage)
  - log["sotif"]["triggering_conditions"]: list of fired TC names
"""

import json
import csv
import argparse
from pathlib import Path
from typing import Any, Dict

from sotif_config import load_config, compute_odd_factor_value, compute_triggering_conditions

CATEGORY_TO_FIELD = {
    "environmental": "odd_env",
    "infrastructure": "odd_infra",
    "traffic": "odd_traffic",
    "operational": "odd_operational",
}

# Approximate reference points for a handful of standard CARLA weather
# presets (cloudiness, precipitation, precipitation_deposits, wetness).
# This is only a "nearest label" heuristic to turn CARLA's raw weather
# floats back into a preset-like name for config lookups; the actual
# scoring per weather value lives in config/sotif_odd_tc.yaml and is meant
# to be reviewed/tuned by the user for their own ODD.
_WEATHER_PRESET_REFERENCE = {
    "ClearNoon": (15.0, 0.0, 0.0, 0.0),
    "CloudyNoon": (80.0, 0.0, 0.0, 0.0),
    "WetNoon": (20.0, 0.0, 50.0, 50.0),
    "SoftRainNoon": (70.0, 15.0, 50.0, 60.0),
    "HardRainNoon": (100.0, 80.0, 90.0, 100.0),
}


def _nearest_weather_preset(world_state: Dict[str, Any]) -> str:
    observed = (
        float(world_state.get("cloudiness", 0.0)),
        float(world_state.get("precipitation", 0.0)),
        float(world_state.get("precipitation_deposits", 0.0)),
        float(world_state.get("wetness", 0.0)),
    )
    best_name, best_dist = "ClearNoon", float("inf")
    for name, ref in _WEATHER_PRESET_REFERENCE.items():
        dist = sum((o - r) ** 2 for o, r in zip(observed, ref))
        if dist < best_dist:
            best_dist = dist
            best_name = name
    return best_name


def _time_of_day_bucket(world_state: Dict[str, Any]) -> str:
    alt = float(world_state.get("sun_altitude_angle", 45.0))
    if alt > 45:
        return "noon"
    if alt > 15:
        return "morning"
    if alt > -10:
        return "dusk"
    return "night"


def _road_condition_bucket(world_state: Dict[str, Any]) -> str:
    wetness = max(
        float(world_state.get("precipitation_deposits", 0.0)),
        float(world_state.get("wetness", 0.0)),
    )
    return "wet" if wetness > 30 else "dry"


def _density_bucket(count: int) -> str:
    if count <= 2:
        return "low"
    if count <= 7:
        return "medium"
    return "high"


def _length_bucket(num_waypoints: int) -> str:
    if num_waypoints < 20:
        return "short"
    if num_waypoints < 50:
        return "medium"
    return "long"


def _speed_bucket(speed_limit_kmh: float) -> str:
    if speed_limit_kmh <= 50:
        return "low"
    if speed_limit_kmh <= 80:
        return "medium"
    return "high"


def _duration_bucket(timeout_s: float) -> str:
    if timeout_s <= 20:
        return "short"
    if timeout_s <= 60:
        return "medium"
    return "long"


def compute_derived_fields(world_state: Dict[str, Any]) -> Dict[str, Any]:
    """Turns the runner's raw world_state facts into the categorical buckets
    referenced by config/sotif_odd_tc.yaml's `derived.*` factor sources."""
    return {
        "weather_preset": _nearest_weather_preset(world_state),
        "time_of_day": _time_of_day_bucket(world_state),
        "road_condition": _road_condition_bucket(world_state),
        "traffic_density": _density_bucket(int(world_state.get("num_npc_vehicles", 0))),
        "pedestrian_density": _density_bucket(int(world_state.get("num_pedestrians", 0))),
        "mission_length_bucket": _length_bucket(int(world_state.get("num_waypoints", 0))),
        "speed_limit_bucket": _speed_bucket(float(world_state.get("ego_speed_limit_kmh") or 50.0)),
        "timeout_bucket": _duration_bucket(float(world_state.get("mission_timeout_s") or 30.0)),
    }


def compute_odd_and_tc(log_data: Dict[str, Any], config: Dict[str, Any]):
    world_state = log_data.get("world_state", {}) or {}
    derived = compute_derived_fields(world_state)

    results = log_data.get("results", {}) or {}
    crit = results.get("critical_metrics", {}) or {}
    dyn = results.get("dynamics_metrics", {}) or {}
    metrics = {
        "min_distance": crit.get("MDBV"),
        "min_ttc": crit.get("min_TTC"),
        "max_ego_speed": dyn.get("max_speed"),
        "avg_ego_speed": dyn.get("mean_speed"),
    }

    context = {"world_state": world_state, "derived": derived, "metrics": metrics}

    odd_values: Dict[str, str] = {}
    category_scores: Dict[str, list] = {f: [] for f in CATEGORY_TO_FIELD.values()}

    for factor in config.get("odd_factors", []):
        value_key, score = compute_odd_factor_value(factor, context)
        odd_values[factor["name"]] = value_key
        field_name = CATEGORY_TO_FIELD.get(factor.get("category"))
        if field_name:
            category_scores[field_name].append(score)

    odd_scores = {
        field_name: (sum(scores) / len(scores) if scores else 0.8)
        for field_name, scores in category_scores.items()
    }
    odd_scores["odd_global"] = sum(odd_scores.values()) / len(odd_scores)

    tcs = compute_triggering_conditions(config.get("triggering_conditions", []), context)

    return odd_scores, odd_values, tcs


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute SOTIF ODD metrics (config-driven)")
    parser.add_argument("--dataset_dir", required=True, help="Path alla cartella dataset da analizzare")
    parser.add_argument("--config", default=None, help="Path a config/sotif_odd_tc.yaml (default: repo config)")
    args = parser.parse_args()

    dataset_dir = Path(args.dataset_dir).resolve()
    if not dataset_dir.exists():
        raise FileNotFoundError(f"Dataset dir non trovata: {dataset_dir}")

    config = load_config(Path(args.config) if args.config else None)

    base_dir = dataset_dir.parents[1]
    dataset_name = dataset_dir.name
    out_csv = base_dir / "datasets" / f"{dataset_name}.csv"

    print(f"[INFO] Dataset       : {dataset_name}")
    print(f"[INFO] Input logs    : {dataset_dir}")
    print(f"[INFO] Output CSV    : {out_csv}")

    files = sorted(dataset_dir.glob("*_log_basic.json"))
    if not files:
        raise SystemExit(f"Nessun *_log_basic.json trovato in {dataset_dir}")

    header = [
        "scenario_id",
        "odd_env",
        "odd_infra",
        "odd_traffic",
        "odd_operational",
        "odd_global",
        "odd_values",
        "triggering_conditions",
    ]

    rows = []
    seen_scenarios = set()

    for idx, path in enumerate(files, start=1):
        print(f"[STEP A] ({idx}/{len(files)}) Elaboro: {path.name}")

        with path.open() as f:
            data = json.load(f)

        scenario_id = data.get("scenario_id", path.stem)
        seen_scenarios.add(scenario_id)

        odd_scores, odd_values, tcs = compute_odd_and_tc(data, config)

        sotif_block = data.get("sotif", {})
        sotif_block.update(odd_scores)
        sotif_block["odd_values"] = odd_values
        sotif_block["triggering_conditions"] = tcs
        data["sotif"] = sotif_block

        with path.open("w") as f:
            json.dump(data, f, indent=2)

        rows.append([
            scenario_id,
            odd_scores["odd_env"],
            odd_scores["odd_infra"],
            odd_scores["odd_traffic"],
            odd_scores["odd_operational"],
            odd_scores["odd_global"],
            json.dumps(odd_values),
            ";".join(tcs),
        ])

    with out_csv.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for r in rows:
            writer.writerow(r)

    print(f"[STEP A] File JSON elaborati: {len(files)}")
    print(f"[STEP A] Scenari distinti trovati: {len(seen_scenarios)}")
    print(f"[STEP A] CSV scritto in: {out_csv}")


if __name__ == "__main__":
    main()
