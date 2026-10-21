"""
analysis/odd_tc_coverage.py

Suite-level ODD-factor / triggering-condition coverage and entropy, computed
against the named categories declared in config/sotif_odd_tc.yaml, over the
whole suite. This is distinct from analysis/rq2_coverage_entropy.py, which
measures diversity over KMeans-*discovered* clusters of continuous
dynamics/criticality metrics - that module stays untouched and still answers
a different, still-useful question.

Three reports are written, in the dataset's own folder:

  odd_coverage_entropy_final_report.csv   one row per ODD factor:
      declared_values, observed_values, coverage, entropy_norm, num_scenarios
  odd_coverage_detail_final_report.csv    one row per (factor, declared value):
      num_scenarios, share_of_scenarios, num_executions
  tc_coverage_detail_final_report.csv     one row per declared TC (same three
      counts), plus a last row ALL_TCS that summarizes all the TCs together
      (declared_tcs, observed_tcs, coverage, entropy_norm)

Coverage: fraction of the declared values (or TCs) observed at least once
across the scenarios.
Entropy: Shannon entropy of the empirical distribution of scenarios over the
declared values (or TCs), normalized by log(#declared) - same normalization
idea as rq2_coverage_entropy.py's _shannon_entropy, reimplemented here
against named categories instead of KMeans cluster ids.

Because ODD/TC are computed per *run* by compute_sotif_odd.py (10 runs per
scenario), scenarios are counted through one annotation per scenario_id: the
union of the ODD categorical values observed across all its runs, and the
union of triggering conditions fired across all its runs (a scenario
"contains" a value or a triggering condition if at least one of its runs
exhibited it). The union matters because a stochastic scenario (e.g. a Scenic
program re-sampled at every run) can land in different ODD values from one
run to the next. Consequently a scenario can count towards several values of
the same factor. The num_executions columns count runs instead of scenarios.

Only the values declared in the configuration are reported: a value observed
in a run but not declared is not counted.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

import numpy as np

sys.path.append(str(Path(__file__).resolve().parents[1] / "data_gathering" / "enriching"))
from sotif_config import load_config, get_odd_factor_values  # noqa: E402

_FILENAME_RE = re.compile(r"(.+)_run_(\d+)_log_basic\.json")

ALL_TCS = "ALL_TCS"

ODD_SUMMARY_FIELDS = ["factor", "declared_values", "observed_values", "coverage", "entropy_norm", "num_scenarios"]
ODD_DETAIL_FIELDS = ["factor", "value", "num_scenarios", "share_of_scenarios", "num_executions"]
TC_DETAIL_FIELDS = ["tc", "num_scenarios", "share_of_scenarios", "num_executions",
                    "declared_tcs", "observed_tcs", "coverage", "entropy_norm"]


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
    odd_values: Dict[str, Set[str]]
    triggering_conditions: List[str]


@dataclass
class ExecutionCounts:
    """How many executions (runs) exhibited each value / TC, over the suite."""
    odd: Dict[str, Dict[str, int]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))
    tc: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    tc_any: int = 0  # executions in which at least one TC fired


def _load_scenario_annotations(dataset_dir: Path):
    grouped: Dict[str, List[Path]] = defaultdict(list)
    for f in sorted(dataset_dir.glob("*_run_*_log_basic.json")):
        m = _FILENAME_RE.match(f.name)
        if not m:
            continue
        grouped[m.group(1)].append(f)

    annotations = []
    executions = ExecutionCounts()
    for scenario_id, run_files in grouped.items():
        odd_values: Dict[str, Set[str]] = defaultdict(set)
        tcs_union: Set[str] = set()
        for path in sorted(run_files):
            with path.open() as fh:
                data = json.load(fh)
            sotif = data.get("sotif", {}) or {}
            for factor, value in (sotif.get("odd_values", {}) or {}).items():
                odd_values[factor].add(value)
                executions.odd[factor][value] += 1
            fired = sotif.get("triggering_conditions", []) or []
            tcs_union.update(fired)
            for tc in fired:
                executions.tc[tc] += 1
            if fired:
                executions.tc_any += 1

        annotations.append(
            ScenarioAnnotation(
                scenario_id=scenario_id,
                odd_values=dict(odd_values),
                triggering_conditions=sorted(tcs_union),
            )
        )

    return annotations, executions


def _share(n: int, total: int) -> float:
    return round(n / total, 4) if total else 0.0


def _compute_odd_rows(annotations, executions: ExecutionCounts, odd_factors_cfg):
    total = len(annotations)
    summary_rows, detail_rows = [], []

    for factor in odd_factors_cfg:
        name = factor["name"]
        declared_values = get_odd_factor_values(factor)
        universe_size = len(declared_values)

        # scenarios in which each declared value was observed (union over runs)
        counts: Dict[str, int] = defaultdict(int)
        for ann in annotations:
            for v in ann.odd_values.get(name, ()):
                if v in declared_values:
                    counts[v] += 1

        coverage = len(counts) / universe_size
        entropy_norm = _shannon_entropy_norm(counts, universe_size)

        summary_rows.append({
            "factor": name,
            "declared_values": universe_size,
            "observed_values": len(counts),
            "coverage": round(coverage, 4),
            "entropy_norm": round(entropy_norm, 4),
            "num_scenarios": total,
        })

        for v in declared_values:
            detail_rows.append({
                "factor": name,
                "value": v,
                "num_scenarios": counts.get(v, 0),
                "share_of_scenarios": _share(counts.get(v, 0), total),
                "num_executions": executions.odd.get(name, {}).get(v, 0),
            })

    return summary_rows, detail_rows


def _compute_tc_rows(annotations, executions: ExecutionCounts, tc_defs_cfg):
    total = len(annotations)
    tc_names = [tc["name"] for tc in tc_defs_cfg]
    universe_size = len(tc_names) if tc_names else 1

    counts: Dict[str, int] = defaultdict(int)
    scenarios_with_any = 0
    for ann in annotations:
        fired = [tc for tc in ann.triggering_conditions if tc in tc_names]
        for tc in fired:
            counts[tc] += 1
        if fired:
            scenarios_with_any += 1

    rows = [{
        "tc": name,
        "num_scenarios": counts.get(name, 0),
        "share_of_scenarios": _share(counts.get(name, 0), total),
        "num_executions": executions.tc.get(name, 0),
    } for name in tc_names]

    rows.append({
        "tc": ALL_TCS,
        "num_scenarios": scenarios_with_any,
        "share_of_scenarios": _share(scenarios_with_any, total),
        "num_executions": executions.tc_any,
        "declared_tcs": universe_size,
        "observed_tcs": len(counts),
        "coverage": round(len(counts) / universe_size, 4),
        "entropy_norm": round(_shannon_entropy_norm(counts, universe_size), 4),
    })
    return rows


def _write(path: Path, fieldnames: List[str], rows: List[Dict]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval="")
        writer.writeheader()
        writer.writerows(rows)


def run(dataset_dir: Path, config_path: Optional[Path] = None, output_dir: Optional[Path] = None) -> Dict[str, Path]:
    dataset_dir = Path(dataset_dir).resolve()
    # written inside the dataset's own output folder, unless overridden
    output_dir = Path(output_dir).resolve() if output_dir else dataset_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    config = load_config(config_path)

    annotations, executions = _load_scenario_annotations(dataset_dir)
    if not annotations:
        raise SystemExit(f"No annotated scenario (sotif.odd_values) found in {dataset_dir}")

    odd_summary, odd_detail = _compute_odd_rows(annotations, executions, config.get("odd_factors", []))
    tc_rows = _compute_tc_rows(annotations, executions, config.get("triggering_conditions", []))

    odd_summary_csv = output_dir / "odd_coverage_entropy_final_report.csv"
    odd_detail_csv = output_dir / "odd_coverage_detail_final_report.csv"
    tc_detail_csv = output_dir / "tc_coverage_detail_final_report.csv"
    _write(odd_summary_csv, ODD_SUMMARY_FIELDS, odd_summary)
    _write(odd_detail_csv, ODD_DETAIL_FIELDS, odd_detail)
    _write(tc_detail_csv, TC_DETAIL_FIELDS, tc_rows)

    print(f"[OK] ODD coverage/entropy (n={len(annotations)} scenarios): {odd_summary_csv}")
    print(f"[OK] ODD coverage detail: {odd_detail_csv}")
    print(f"[OK] TC coverage detail and summary: {tc_detail_csv}")

    return {"odd_summary": odd_summary_csv, "odd_detail": odd_detail_csv, "tc_detail": tc_detail_csv}


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute ODD/TC coverage and entropy for a dataset")
    parser.add_argument("--dataset_dir", required=True, help="Path to the dataset folder to analyze")
    parser.add_argument("--config", default=None, help="Path to config/sotif_odd_tc.yaml (default: repo config)")
    parser.add_argument("--output_dir", default=None, help="Where to save the CSVs (default: the dataset's own folder)")
    args = parser.parse_args()

    run(
        dataset_dir=Path(args.dataset_dir),
        config_path=Path(args.config) if args.config else None,
        output_dir=Path(args.output_dir) if args.output_dir else None,
    )


if __name__ == "__main__":
    main()
