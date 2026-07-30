"""
sotif_config.py

Shared loader/evaluator for config/sotif_odd_tc.yaml, the user-editable
definition of ODD factors, triggering conditions, and the hazard acceptance
threshold. Used by compute_sotif_odd.py, compute_sotif_hazard.py, and
src/analysis/odd_tc_coverage.py so none of them hardcode the ODD/TC taxonomy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "sotif_odd_tc.yaml"


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise FileNotFoundError(f"SOTIF ODD/TC config non trovato: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_acceptance_threshold(config: Dict[str, Any], default: float = 0.2) -> float:
    return float(config.get("hazards", {}).get("acceptance_threshold", default))


def resolve_field(context: Dict[str, Any], dotted_path: str) -> Any:
    """Resolve a dotted path like 'world_state.weather_preset' against a
    context dict shaped {"world_state": {...}, "derived": {...}, "metrics": {...}}."""
    node: Any = context
    for part in dotted_path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


_OPS = {
    "lt": lambda a, b: a is not None and a < b,
    "lte": lambda a, b: a is not None and a <= b,
    "gt": lambda a, b: a is not None and a > b,
    "gte": lambda a, b: a is not None and a >= b,
    "eq": lambda a, b: a == b,
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
}


def evaluate_predicate(pred: Dict[str, Any], context: Dict[str, Any]) -> bool:
    field = pred.get("field")
    op = pred.get("op")
    expected = pred.get("value")

    if op not in _OPS:
        raise ValueError(f"Operatore predicato sconosciuto: {op}")

    actual = resolve_field(context, field)
    try:
        return bool(_OPS[op](actual, expected))
    except TypeError:
        # tipico caso: confronto numerico con None/valore non comparabile
        return False


def compute_odd_factor_value(factor: Dict[str, Any], context: Dict[str, Any]) -> (str, float):
    """Returns (matched_value_key, score) for one configured ODD factor."""
    raw_value = resolve_field(context, factor["source"])
    values: Dict[str, float] = factor.get("values", {})
    default_score = float(factor.get("default", 0.7))

    key = str(raw_value) if raw_value is not None else None
    if key is not None and key in values:
        return key, float(values[key])
    return (key if key is not None else "unknown"), default_score


def compute_triggering_conditions(
    tc_defs: List[Dict[str, Any]], context: Dict[str, Any]
) -> List[str]:
    fired = []
    for tc in tc_defs:
        predicates = tc.get("all_of", [])
        if predicates and all(evaluate_predicate(p, context) for p in predicates):
            fired.append(tc["name"])
    return sorted(fired)
