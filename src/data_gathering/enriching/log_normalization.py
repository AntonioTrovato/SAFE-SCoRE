"""log_normalization.py

Utilities to make base/enriched logs *consistent*.

Real (and annoying) problem: logs can mix "local" frames (0..N)
with carla_frame (snapshot.frame, huge values), and timestamps that are
sometimes relative to the run and sometimes absolute (simulator uptime).

These functions normalize:
 - event names (red_lights -> red_light)
 - event frames (if it looks like a carla_frame, map it to the local frame)
 - event timestamps (if implausible, recompute them from the frames)
 - the position of the events node (duplicated under results for compatibility)

Does not require re-running the simulations.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple, Optional


EVENT_ALIASES = {
    "red_lights": "red_light",
    "red_light": "red_light",
    "stop": "stop_sign",
    "stop_sign": "stop_sign",
}


def _as_float(x: Any) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None


def _as_int(x: Any) -> Optional[int]:
    try:
        return int(x)
    except Exception:
        return None


def build_frame_index(frames: List[Dict[str, Any]]) -> Tuple[Dict[int, Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    """Returns (by_local_frame, by_carla_frame)."""
    by_local: Dict[int, Dict[str, Any]] = {}
    by_carla: Dict[int, Dict[str, Any]] = {}

    for idx, fr in enumerate(frames):
        lf = _as_int(fr.get("frame", idx))
        if lf is None:
            continue
        by_local[lf] = fr

        cf = _as_int(fr.get("carla_frame"))
        if cf is not None:
            by_carla[cf] = fr

    return by_local, by_carla


def normalize_event_key(k: str) -> str:
    return EVENT_ALIASES.get(k, k)


def normalize_events(log: Dict[str, Any], *, max_timestamp_factor: float = 10.0) -> Dict[str, Any]:
    """Normalizes log["events"] (in-place) and ensures compatibility.

    max_timestamp_factor: if an event timestamp is > (simulated_time * factor) it is considered "implausible".
    """

    frames: List[Dict[str, Any]] = log.get("frames", []) or []
    by_local, by_carla = build_frame_index(frames)

    results = log.get("results", {}) or {}
    sim_t = _as_float(results.get("total_simulation_time"))
    if sim_t is None:
        # fallback: last frame timestamp
        if frames:
            sim_t = _as_float(frames[-1].get("timestamp"))
        if sim_t is None:
            sim_t = 0.0

    events = log.get("events", {}) or {}
    normalized: Dict[str, Any] = {}

    for raw_k, ev_list in events.items():
        k = normalize_event_key(str(raw_k))
        if not isinstance(ev_list, list):
            # keep as-is
            normalized[k] = ev_list
            continue

        out_list = []
        for ev in ev_list:
            if not isinstance(ev, dict):
                out_list.append(ev)
                continue

            ev2 = dict(ev)

            # frame: if it is a carla_frame (typically very large), map it
            fr = _as_int(ev2.get("frame"))
            if fr is not None:
                # Heuristic: if fr does not exist among local frames but exists among carla frames, it's a carla_frame
                if fr not in by_local and fr in by_carla:
                    ev2["carla_frame"] = fr
                    ev2["frame"] = _as_int(by_carla[fr].get("frame"))

            # timestamp: if implausible, recompute it from the local frame
            ts = _as_float(ev2.get("timestamp"))
            lf = _as_int(ev2.get("frame"))
            if lf is not None and lf in by_local:
                fr_ts = _as_float(by_local[lf].get("timestamp"))
            else:
                fr_ts = None

            if ts is None or (sim_t > 0 and ts > sim_t * max_timestamp_factor):
                if fr_ts is not None:
                    ev2["timestamp"] = fr_ts
            else:
                # if the timestamp exists and frame_ts is close, ok. If it diverges a lot, prefer frame_ts
                if fr_ts is not None and abs(ts - fr_ts) > max(5.0, sim_t * 0.25):
                    ev2["timestamp"] = fr_ts

            out_list.append(ev2)

        # concatenate if entries already exist (e.g. red_lights + red_light)
        if k in normalized and isinstance(normalized[k], list):
            normalized[k].extend(out_list)
        else:
            normalized[k] = out_list

    # write back
    log["events"] = normalized

    # compat: duplicate under results
    results = log.setdefault("results", {})
    results["events"] = normalized

    return log


def ensure_event_counts_schema(log: Dict[str, Any]) -> Dict[str, Any]:
    """Ensures results.event_counts has the main keys used downstream."""
    results = log.setdefault("results", {})
    counts = results.setdefault("event_counts", {})

    # alias: if red_lights exists but not red_light
    if "red_light" not in counts and "red_lights" in counts:
        counts["red_light"] = counts.get("red_lights", 0)
    if "stop_sign" not in counts and "stop" in counts:
        counts["stop_sign"] = counts.get("stop", 0)

    return log
