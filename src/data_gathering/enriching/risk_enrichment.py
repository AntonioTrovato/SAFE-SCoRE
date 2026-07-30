import argparse
import json
import os
from typing import Dict, Any

from collections import defaultdict

from log_normalization import normalize_events, ensure_event_counts_schema

HAZARDS = ["collision", "red_light", "speeding", "stop_sign"]


def list_json_files(input_dir: str):
    files = sorted(
        os.path.join(input_dir, f)
        for f in os.listdir(input_dir)
        if f.endswith("_log_basic.json")
    )
    return files


def load_run(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    normalize_events(data)
    ensure_event_counts_schema(data)
    return data


def estimate_collision_severity(events_collision):
    if not events_collision:
        return 0

    severities = []

    for ev in events_collision:
        try:
            ego = ev["actors_involved"]["ego"]
            other = ev["actors_involved"]["other_actor"]

            v_ego = float(ego.get("speed_mps", 0.0))
            v_other = float(other.get("speed_mps", 0.0))
            type_other = str(other.get("type_id", ""))

            v_rel_mps = abs(v_ego - v_other)
            v_rel_kmh = v_rel_mps * 3.6

            if v_rel_kmh < 15:
                s = 1
            elif v_rel_kmh < 40:
                s = 2
            else:
                s = 3

            if "walker." in type_other or "bike" in type_other or "bicycle" in type_other:
                s = min(3, s + 1)

            severities.append(s)
        except Exception:
            severities.append(1)

    return max(severities) if severities else 0


def estimate_red_light_severity(events_red):
    if not events_red:
        return 0
    severities = []
    for ev in events_red:
        speed_kmh = float(ev.get("speed_kmh", 0.0))
        if speed_kmh > 30:
            severities.append(2)
        else:
            severities.append(1)
    return max(severities) if severities else 0


def estimate_speeding_severity(events_speed):
    if not events_speed:
        return 0
    severities = []
    for ev in events_speed:
        speed = float(ev.get("speed_kmh", 0.0))
        limit = float(ev.get("speed_limit_kmh", 0.0))
        delta = speed - limit
        if delta > 30:
            severities.append(2)
        else:
            severities.append(1)
    return max(severities) if severities else 0


def estimate_stop_severity(events_stop):
    if not events_stop:
        return 0
    return 1  # for now, all S1


def extract_run_hazard_info(run_data: Dict[str, Any]) -> Dict[str, Any]:
    results = run_data.get("results", {})
    events = run_data.get("events", {})

    has_collision = bool(results.get("has_collision", False))
    has_red = bool(results.get("has_red_light_violation", False))
    has_speeding = bool(results.get("has_speeding", False))
    has_stop = bool(results.get("has_stop_violation", False))

    collision_events = events.get("collision", [])
    red_events = events.get("red_light", []) or events.get("red_lights", [])
    speed_events = events.get("speeding", [])
    stop_events = events.get("stop_sign", [])

    s_collision = estimate_collision_severity(collision_events) if has_collision else 0
    s_red = estimate_red_light_severity(red_events) if has_red else 0
    s_speed = estimate_speeding_severity(speed_events) if has_speeding else 0
    s_stop = estimate_stop_severity(stop_events) if has_stop else 0

    return {
        "has": {
            "collision": has_collision,
            "red_light": has_red,
            "speeding": has_speeding,
            "stop_sign": has_stop,
        },
        "severity": {
            "collision": s_collision,
            "red_light": s_red,
            "speeding": s_speed,
            "stop_sign": s_stop,
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="Compute residual risk (P_h, S_h, R_h) for ScenarioFuzzLLM (streaming, low RAM)"
    )
    parser.add_argument("--input_dir", required=True,
                        help="Directory with the *_log_basic.json files")
    parser.add_argument("--output_csv", required=True,
                        help="Path of the output CSV")
    args = parser.parse_args()

    files = list_json_files(args.input_dir)
    if not files:
        print(f"[risk_enrichment] No *_log_basic.json found in {args.input_dir}")
        return

    total_files = len(files)
    print(f"[risk_enrichment] Found {total_files} files to process in {args.input_dir}")

    # aggregator: (generation_id, scenario_id) -> stats
    agg: Dict[tuple, Dict[str, Any]] = {}

    for idx, path in enumerate(files, start=1):
        fname = os.path.basename(path)
        print(f"[risk_enrichment] ({idx}/{total_files}) Processing: {fname}")

        run_data = load_run(path)

        gen_id = str(run_data.get("generation_id", "unknown"))
        scen_id = str(run_data.get("scenario_id", "unknown"))
        key = (gen_id, scen_id)

        if key not in agg:
            tool = str(run_data.get("tool", "ScenarioFuzzLLM"))
            map_name = str(run_data.get("map_name", run_data.get("results", {}).get("map_name", "unknown")))
            agg[key] = {
                "tool": tool,
                "map_name": map_name,
                "n_runs": 0,
                "hazard_counts": {h: 0 for h in HAZARDS},
                "severity_sum": {h: 0.0 for h in HAZARDS},
                "severity_max": {h: 0.0 for h in HAZARDS},
            }
            print(f"[risk_enrichment]   New aggregated scenario: generation={gen_id}, scenario_id={scen_id}")

        info = extract_run_hazard_info(run_data)
        has = info["has"]
        sev = info["severity"]

        st = agg[key]
        st["n_runs"] += 1

        for h in HAZARDS:
            if has[h]:
                st["hazard_counts"][h] += 1
                st["severity_sum"][h] += sev[h]
                if sev[h] > st["severity_max"][h]:
                    st["severity_max"][h] = sev[h]

    print(f"[risk_enrichment] Aggregation completed for {len(agg)} scenarios. Writing CSV...")

    os.makedirs(os.path.dirname(args.output_csv), exist_ok=True)
    with open(args.output_csv, "w", encoding="utf-8") as out:
        header = [
            "tool",
            "generation_id",
            "scenario_id",
            "map_name",
            "n_runs",
            "hazard",
            "n_hazard_runs",
            "P_h",
            "S_mean",
            "S_max",
            "R_mean",
            "R_max",
        ]
        out.write(",".join(header) + "\n")

        for (gen_id, scen_id), st in agg.items():
            tool = st["tool"]
            map_name = st["map_name"]
            n_runs = st["n_runs"]

            for h in HAZARDS:
                n_h = st["hazard_counts"][h]
                P_h = n_h / n_runs if n_runs > 0 else 0.0

                if n_h > 0:
                    S_mean = st["severity_sum"][h] / n_h
                else:
                    S_mean = 0.0

                S_max = st["severity_max"][h]
                R_mean = P_h * S_mean
                R_max = P_h * S_max

                row = [
                    tool,
                    gen_id,
                    scen_id,
                    map_name,
                    str(n_runs),
                    h,
                    str(n_h),
                    f"{P_h:.4f}",
                    f"{S_mean:.4f}",
                    f"{S_max:.4f}",
                    f"{R_mean:.4f}",
                    f"{R_max:.4f}",
                ]
                out.write(",".join(row) + "\n")

    print(f"[risk_enrichment] CSV saved to: {args.output_csv}")


if __name__ == "__main__":
    main()