"""
analysis/odd_tc_coverage.py

Suite-level ODD-factor / triggering-condition coverage and entropy, computed
against the named categories declared in config/sotif_odd_tc.yaml. This is
distinct from analysis/rq2_coverage_entropy.py, which measures diversity over
KMeans-*discovered* clusters of continuous dynamics/criticality metrics -
that module stays untouched and still answers a different, still-useful
question.

Coverage (per ODD factor): fraction of the factor's declared values observed
at least once across the scenarios considered.
Entropy (per ODD factor): Shannon entropy of the empirical distribution of
scenarios over the factor's declared values, normalized by log(#declared
values) - same normalization idea as rq2_coverage_entropy.py's
_shannon_entropy, reimplemented here against named categories instead of
KMeans cluster ids.
Same two numbers are computed once for triggering conditions as a whole
(coverage/entropy over the declared TC names).

Both are computed twice per dataset: once over all scenarios, once
restricted to scenarios flagged is_non_acceptable in
<dataset>_sotif_hazard_leaderboard.csv (produced by compute_sotif_hazard.py).

Because ODD/TC are computed per *run* by compute_sotif_odd.py (10 runs per
scenario), this module first aggregates to one annotation per scenario_id:
the ODD categorical values from its first run (scenario-design properties
that shouldn't vary meaningfully across repeated stochastic runs in this
corpus), and the union of triggering conditions fired across all its runs
(a scenario "contains" a triggering condition if at least one of its runs
exhibited it).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

import numpy as np

sys.path.append(str(Path(__file__).resolve().parents[1] / "data_gathering" / "enriching"))
from sotif_config import load_config  # noqa: E402

_FILENAME_RE = re.compile(r"(.+)_run_(\d+)_log_basic\.json")


def _shannon_entropy_norm(counts: Dict[str, int], universe_size: int) -> float:
    total = sum(counts.values())
    if total == 0 or universe_size <= 1:
        return 0.0
    probs = np.array([c / total for c in counts.values() if c > 0])
    entropy = abs(float(-(probs * np.log(probs)).sum()))  # avoid -0.0 from exact single-category logs
    return entropy / np.log(universe_size)


@dataclass
class ScenarioAnnotation:
    scenario_id: str
    odd_values: Dict[str, str]
    triggering_conditions: List[str]
    is_non_acceptable: bool


def _read_non_acceptable_ids(hazard_csv: Path) -> Set[str]:
    ids: Set[str] = set()
    if not hazard_csv.exists():
        return ids
    with hazard_csv.open(newline="") as f:
        for row in csv.DictReader(f):
            if str(row.get("is_non_acceptable", "")).strip().lower() in ("true", "1"):
                ids.add(row["scenario_id"])
    return ids


def _load_scenario_annotations(dataset_dir: Path, non_acceptable_ids: Set[str]) -> List[ScenarioAnnotation]:
    grouped: Dict[str, List[Path]] = defaultdict(list)
    for f in sorted(dataset_dir.glob("*_run_*_log_basic.json")):
        m = _FILENAME_RE.match(f.name)
        if not m:
            continue
        grouped[m.group(1)].append(f)

    annotations = []
    for scenario_id, run_files in grouped.items():
        odd_values: Dict[str, str] = {}
        tcs_union: Set[str] = set()
        for i, path in enumerate(sorted(run_files)):
            with path.open() as fh:
                data = json.load(fh)
            sotif = data.get("sotif", {}) or {}
            if i == 0:
                odd_values = sotif.get("odd_values", {}) or {}
            tcs_union.update(sotif.get("triggering_conditions", []) or [])

        annotations.append(
            ScenarioAnnotation(
                scenario_id=scenario_id,
                odd_values=odd_values,
                triggering_conditions=sorted(tcs_union),
                is_non_acceptable=scenario_id in non_acceptable_ids,
            )
        )

    return annotations


def _compute_rows(annotations: List[ScenarioAnnotation], odd_factors_cfg, tc_defs_cfg) -> List[Dict]:
    rows = []

    for factor in odd_factors_cfg:
        name = factor["name"]
        declared_values = list(factor.get("values", {}).keys())
        universe_size = len(declared_values) if declared_values else 1

        counts: Dict[str, int] = defaultdict(int)
        for ann in annotations:
            v = ann.odd_values.get(name)
            if v in declared_values:
                counts[v] += 1

        coverage = (len(counts) / universe_size) if universe_size else 0.0
        entropy_norm = _shannon_entropy_norm(counts, universe_size)

        rows.append({
            "kind": "odd_factor",
            "name": name,
            "declared_values": universe_size,
            "observed_values": len(counts),
            "coverage": round(coverage, 4),
            "entropy_norm": round(entropy_norm, 4),
            "num_scenarios": len(annotations),
        })

    tc_names = [tc["name"] for tc in tc_defs_cfg]
    universe_size = len(tc_names) if tc_names else 1
    counts = defaultdict(int)
    for ann in annotations:
        for tc in ann.triggering_conditions:
            if tc in tc_names:
                counts[tc] += 1

    coverage = (len(counts) / universe_size) if universe_size else 0.0
    entropy_norm = _shannon_entropy_norm(counts, universe_size)
    rows.append({
        "kind": "triggering_condition",
        "name": "ALL_TCS",
        "declared_values": universe_size,
        "observed_values": len(counts),
        "coverage": round(coverage, 4),
        "entropy_norm": round(entropy_norm, 4),
        "num_scenarios": len(annotations),
    })

    return rows


def run(dataset_dir: Path, config_path: Optional[Path] = None, output_dir: Optional[Path] = None) -> Dict[str, Path]:
    dataset_dir = Path(dataset_dir).resolve()
    dataset_name = dataset_dir.name
    base_dir = dataset_dir.parents[1]
    output_dir = Path(output_dir).resolve() if output_dir else (base_dir / "datasets")
    output_dir.mkdir(parents=True, exist_ok=True)

    config = load_config(config_path)
    hazard_csv = base_dir / "datasets" / f"{dataset_name}_sotif_hazard_leaderboard.csv"
    non_acceptable_ids = _read_non_acceptable_ids(hazard_csv)

    annotations = _load_scenario_annotations(dataset_dir, non_acceptable_ids)
    if not annotations:
        raise SystemExit(f"Nessuno scenario annotato (sotif.odd_values) trovato in {dataset_dir}")

    odd_factors_cfg = config.get("odd_factors", [])
    tc_defs_cfg = config.get("triggering_conditions", [])

    all_rows = _compute_rows(annotations, odd_factors_cfg, tc_defs_cfg)
    non_acc_annotations = [a for a in annotations if a.is_non_acceptable]
    non_acc_rows = (
        _compute_rows(non_acc_annotations, odd_factors_cfg, tc_defs_cfg) if non_acc_annotations else []
    )

    fieldnames = ["kind", "name", "declared_values", "observed_values", "coverage", "entropy_norm", "num_scenarios"]

    all_csv = output_dir / f"{dataset_name}_odd_tc_coverage_all.csv"
    with all_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    non_acc_csv = output_dir / f"{dataset_name}_odd_tc_coverage_non_acceptable.csv"
    with non_acc_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(non_acc_rows)

    print(f"[OK] Coverage/entropy (tutti gli scenari, n={len(annotations)}): {all_csv}")
    print(f"[OK] Coverage/entropy (scenari non-acceptable, n={len(non_acc_annotations)}): {non_acc_csv}")

    return {"all": all_csv, "non_acceptable": non_acc_csv}


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute ODD/TC coverage and entropy for a dataset")
    parser.add_argument("--dataset_dir", required=True, help="Path alla cartella dataset da analizzare")
    parser.add_argument("--config", default=None, help="Path a config/sotif_odd_tc.yaml (default: repo config)")
    parser.add_argument("--output_dir", default=None, help="Dove salvare i CSV (default: datasets/)")
    args = parser.parse_args()

    run(
        dataset_dir=Path(args.dataset_dir),
        config_path=Path(args.config) if args.config else None,
        output_dir=Path(args.output_dir) if args.output_dir else None,
    )


if __name__ == "__main__":
    main()
