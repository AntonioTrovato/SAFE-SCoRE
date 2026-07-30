"""
feature_extraction_fast.py
Extracts numerical features from enriched logs (standard JSON) to build feature vectors (RQ3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, List
import json


def _safe_get(d: Dict[str, Any], path: List[str], default=None):
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _to_float(x, default=None):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _to_int(x, default=0):
    try:
        if x is None:
            return default
        return int(x)
    except Exception:
        return default


def _min_timestamp(events: Any) -> Optional[float]:
    if not isinstance(events, list) or not events:
        return None
    ts = []
    for e in events:
        if isinstance(e, dict) and "timestamp" in e:
            v = _to_float(e.get("timestamp"))
            if v is not None:
                ts.append(v)
    return min(ts) if ts else None


@dataclass
class FeatureVector:
    scenario_id: str
    tool: str
    generation_id: str
    map_name: str
    run_index: int

    mean_speed: Optional[float] = None
    max_speed: Optional[float] = None
    mean_long_acc: Optional[float] = None
    p95_long_acc: Optional[float] = None
    max_long_acc: Optional[float] = None

    min_ttc: Optional[float] = None
    mdbv: Optional[float] = None
    tet_total: Optional[float] = None
    tet_max: Optional[float] = None

    collision_count: int = 0
    red_light_count: int = 0
    stop_sign_count: int = 0
    speeding_count: int = 0
    lane_invasion_count: int = 0
    off_road_count: int = 0

    time_to_first_hazard: Optional[float] = None
    time_to_first_collision: Optional[float] = None
    time_to_first_lane_invasion: Optional[float] = None
    time_to_first_off_road: Optional[float] = None

    completion_rate: Optional[float] = None
    actual_distance_traveled: Optional[float] = None
    max_progress_reached: Optional[float] = None

    odd_global: Optional[float] = None
    tc_count: int = 0


class EnrichedLogFeatureExtractorFast:
    def parse_log(self, raw_text: str) -> Dict[str, Any]:
        # plain JSON
        return json.loads(raw_text)

    def extract(self, log: Dict[str, Any]) -> FeatureVector:
        tool = str(log.get("tool", "UNKNOWN"))
        generation_id = str(log.get("generation_id", ""))
        scenario_id = str(log.get("scenario_id", ""))
        map_name = str(log.get("map_name", ""))
        run_index = _to_int(log.get("run_index", 0), 0)

        event_counts = _safe_get(log, ["results", "event_counts"], {}) or {}
        # Events in the logs are top-level (log["events"]).
        # For compatibility we also keep results.events if present.
        events_root = _safe_get(log, ["events"], None)
        if not isinstance(events_root, dict):
            events_root = _safe_get(log, ["results", "events"], {}) or {}

        ts_collision = _min_timestamp(events_root.get("collision"))
        ts_lane = _min_timestamp(events_root.get("lane_invasion"))
        ts_off = _min_timestamp(events_root.get("off_road"))
        ts_red = _min_timestamp(events_root.get("red_light") or events_root.get("red_lights"))
        ts_stop = _min_timestamp(events_root.get("stop_sign") or events_root.get("stop"))
        ts_speed = _min_timestamp(events_root.get("speeding"))

        all_ts = [t for t in [ts_collision, ts_lane, ts_off, ts_red, ts_stop, ts_speed] if t is not None]
        ts_first_hazard = min(all_ts) if all_ts else None

        tcs = _safe_get(log, ["sotif", "triggering_conditions"], []) or []
        tc_count = len([x for x in tcs if str(x).strip()])

        return FeatureVector(
            scenario_id=scenario_id,
            tool=tool,
            generation_id=generation_id,
            map_name=map_name,
            run_index=run_index,

            mean_speed=_to_float(_safe_get(log, ["results", "dynamics_metrics", "mean_speed"]))
                      or _to_float(_safe_get(log, ["results", "mean_speed"])),
            max_speed=_to_float(_safe_get(log, ["results", "dynamics_metrics", "max_speed"]))
                     or _to_float(_safe_get(log, ["results", "max_speed"])),
            mean_long_acc=_to_float(_safe_get(log, ["results", "dynamics_metrics", "mean_long_acc"]))
                          or _to_float(_safe_get(log, ["results", "mean_long_acc"])),
            p95_long_acc=_to_float(_safe_get(log, ["results", "dynamics_metrics", "p95_long_acc"]))
                         or _to_float(_safe_get(log, ["results", "p95_long_acc"])),
            max_long_acc=_to_float(_safe_get(log, ["results", "dynamics_metrics", "max_long_acc"]))
                         or _to_float(_safe_get(log, ["results", "max_long_acc"])),

            min_ttc=_to_float(_safe_get(log, ["results", "critical_metrics", "min_TTC"]))
                    or _to_float(_safe_get(log, ["results", "min_TTC"]))
                    or _to_float(_safe_get(log, ["results", "min_ttc"])),
            mdbv=_to_float(_safe_get(log, ["results", "critical_metrics", "MDBV"]))
                 or _to_float(_safe_get(log, ["results", "MDBV"]))
                 or _to_float(_safe_get(log, ["results", "mdbv"])),
            tet_total=_to_float(_safe_get(log, ["results", "critical_metrics", "TET_total"]))
                      or _to_float(_safe_get(log, ["results", "TET_total"]))
                      or _to_float(_safe_get(log, ["results", "tet_total"])),
            tet_max=_to_float(_safe_get(log, ["results", "critical_metrics", "TET_max"]))
                    or _to_float(_safe_get(log, ["results", "TET_max"]))
                    or _to_float(_safe_get(log, ["results", "tet_max"])),

            collision_count=_to_int(event_counts.get("collision"), 0),
            red_light_count=_to_int(event_counts.get("red_light") or event_counts.get("red_lights"), 0),
            stop_sign_count=_to_int(event_counts.get("stop_sign") or event_counts.get("stop"), 0),
            speeding_count=_to_int(event_counts.get("speeding"), 0),
            lane_invasion_count=_to_int(event_counts.get("lane_invasion"), 0),
            off_road_count=_to_int(event_counts.get("off_road"), 0),

            time_to_first_hazard=ts_first_hazard,
            time_to_first_collision=ts_collision,
            time_to_first_lane_invasion=ts_lane,
            time_to_first_off_road=ts_off,

            completion_rate=_to_float(_safe_get(log, ["results", "functional_metrics", "performance", "completion_rate"]))
                           or _to_float(_safe_get(log, ["results", "completion_rate"])),
            actual_distance_traveled=_to_float(_safe_get(log, ["results", "functional_metrics", "performance", "actual_distance_traveled"]))
                                    or _to_float(_safe_get(log, ["results", "actual_distance_traveled"])),
            max_progress_reached=_to_float(_safe_get(log, ["results", "functional_metrics", "performance", "max_progress_reached"]))
                               or _to_float(_safe_get(log, ["results", "max_progress_reached"])),

            odd_global=_to_float(_safe_get(log, ["sotif", "odd_global"])),
            tc_count=tc_count,
        )