"""
analysis/rq3_event_percentages.py

RQ3 - Part 3:
- Per-hazard percentages per tool (aggregated over scenarios+runs, weighted by num_runs)
- + % of scenarios with at least one hazard (at least one P_* > 0 in the scenario)

Input:
- leaderboard_files: list of paths to files:
    <Tool>_metrics_sotif_hazard_leaderboard.csv
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd


@dataclass
class RQ3Part3Result:
    output_csv_path: Path


class RQ3EventPercentagesAnalyzer:
    def __init__(self, leaderboard_files: List[str], tool_names: Optional[List[str]] = None):
        if not leaderboard_files:
            raise ValueError("leaderboard_files is empty.")
        self.leaderboard_files = [Path(p).resolve() for p in leaderboard_files]
        for p in self.leaderboard_files:
            if not p.exists():
                raise FileNotFoundError(f"File not found: {p}")

        if tool_names is not None:
            if len(tool_names) != len(leaderboard_files):
                raise ValueError("tool_names must have the same length as leaderboard_files.")
            self.tool_names = tool_names
        else:
            self.tool_names = [self._infer_tool_name(p) for p in self.leaderboard_files]

    @staticmethod
    def _infer_tool_name(p: Path) -> str:
        suffix = "_metrics_sotif_hazard_leaderboard.csv"
        if p.name.endswith(suffix):
            return p.name.replace(suffix, "")
        return p.stem

    @staticmethod
    def _get_p_cols(df: pd.DataFrame) -> List[str]:
        p_cols = [c for c in df.columns if c.startswith("P_")]
        if not p_cols:
            raise RuntimeError("No 'P_*' column found in the leaderboard CSV.")
        return p_cols

    @staticmethod
    def _compute_weighted_percentages(df: pd.DataFrame, p_cols: List[str]) -> Dict[str, float]:
        if "num_runs" not in df.columns:
            raise RuntimeError("Column 'num_runs' missing in the leaderboard CSV.")

        total_runs = df["num_runs"].sum()
        if total_runs == 0:
            raise RuntimeError("Sum of num_runs = 0, cannot compute percentages.")

        res: Dict[str, float] = {}
        for c in p_cols:
            weighted = (df[c] * df["num_runs"]).sum() / total_runs
            res[c] = float(weighted * 100.0)
        return res

    @staticmethod
    def _compute_pct_scenarios_with_any_hazard(df: pd.DataFrame, p_cols: List[str]) -> float:
        # scenario is "hazardous" if at least one P_* > 0
        any_hazard = (df[p_cols] > 0).any(axis=1)
        pct = 100.0 * any_hazard.mean()
        return float(pct)

    @staticmethod
    def _prettify_column(col: str) -> str:
        return "% " + col.replace("P_", "").replace("_", " ").title()

    def run(self, output_dir: str) -> RQ3Part3Result:
        output_path = Path(output_dir).resolve()
        output_path.mkdir(parents=True, exist_ok=True)

        rows = []
        all_cols = set()

        for tool, path in zip(self.tool_names, self.leaderboard_files):
            df = pd.read_csv(path)
            p_cols = self._get_p_cols(df)

            # 1) Per-hazard percentages (weighted by num_runs)
            perc = self._compute_weighted_percentages(df, p_cols)

            # 2) % of scenarios with at least one hazard
            pct_any = self._compute_pct_scenarios_with_any_hazard(df, p_cols)

            row = {"tool": tool, "% Scenarios with ≥1 hazard": round(pct_any, 2)}

            for k, v in perc.items():
                pretty = self._prettify_column(k)
                row[pretty] = round(v, 2)
                all_cols.add(pretty)

            rows.append(row)

        hazard_cols = sorted(all_cols)
        out_df = pd.DataFrame(rows)
        out_df = out_df[["tool", "% Scenarios with ≥1 hazard"] + hazard_cols].sort_values("tool")

        out_csv = output_path / "rq3_event_percentages.csv"
        out_df.to_csv(out_csv, index=False)

        return RQ3Part3Result(output_csv_path=out_csv)