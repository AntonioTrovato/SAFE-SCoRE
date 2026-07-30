"""
analysis/rq4_driving_style_non_collision.py

RQ (new): non-collision hazards that describe the "driving style".
Output:
1) mean/std of the number of hazards (driving-style, non-collision) that appear per scenario
2) % of scenarios in which each driving-style hazard appears (P_h > 0)
3) Radar chart comparing tools on the per-hazard %

Input:
- leaderboard_files: list of paths to the files:
    <Tool>_metrics_sotif_hazard_leaderboard.csv

Expected columns:
- scenario_id (or similar)
- P_* (probability/fraction of runs with hazard per scenario)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Dict, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


@dataclass
class RQ4Result:
    summary_csv_path: Path
    per_scenario_csv_path: Path
    per_hazard_csv_path: Path
    radar_plot_path: Path


class RQ4DrivingStyleNonCollisionAnalyzer:
    def __init__(
        self,
        leaderboard_files: List[str],
        tool_names: Optional[List[str]] = None,
        scenario_id_col: str = "scenario_id",
        p_threshold: float = 0.0,
        driving_hazards: Optional[List[str]] = None,
    ):
        """
        driving_hazards:
          - None => takes all P_* columns excluding P_collision*
          - or a list of hazard base names without the P_ prefix, e.g.:
              ["lane_invasion", "off_road", "red_light", "stop_sign"]
        p_threshold:
          - 0.0 => counts a hazard if P_h > 0 (it occurred at least once in the scenario)
        """
        if not leaderboard_files:
            raise ValueError("leaderboard_files is empty.")

        self.files = [Path(p).resolve() for p in leaderboard_files]
        for p in self.files:
            if not p.exists():
                raise FileNotFoundError(f"File not found: {p}")

        if tool_names is not None:
            if len(tool_names) != len(leaderboard_files):
                raise ValueError("tool_names must have the same length as leaderboard_files.")
            self.tool_names = tool_names
        else:
            self.tool_names = [self._infer_tool_name(p) for p in self.files]

        self.scenario_id_col = scenario_id_col
        self.p_threshold = float(p_threshold)
        self.driving_hazards = driving_hazards

    @staticmethod
    def _infer_tool_name(p: Path) -> str:
        suffix = "_metrics_sotif_hazard_leaderboard.csv"
        if p.name.endswith(suffix):
            return p.name.replace(suffix, "")
        return p.stem

    @staticmethod
    def _pick_scenario_col(df: pd.DataFrame, preferred: str) -> str:
        if preferred in df.columns:
            return preferred
        for alt in ["scenario", "scenario_idx", "scenarioId", "scenario_id", "ScenarioId"]:
            if alt in df.columns:
                return alt
        raise RuntimeError("Missing a scenario_id column (or alternative) in the leaderboard CSV.")

    @staticmethod
    def _pick_driving_p_cols(df: pd.DataFrame, driving_hazards: Optional[List[str]]) -> List[str]:
        p_cols = [c for c in df.columns if c.startswith("P_")]
        if not p_cols:
            raise RuntimeError("No P_* column found in the leaderboard CSV.")

        if driving_hazards is None:
            cols = [c for c in p_cols if not c.startswith("P_collision")]
        else:
            wanted = {f"P_{h}" for h in driving_hazards}
            cols = [c for c in p_cols if c in wanted]

        if not cols:
            raise RuntimeError("No 'driving style' hazard column selected.")
        return sorted(cols)

    @staticmethod
    def _pretty_hazard(p_col: str) -> str:
        return p_col.replace("P_", "").replace("_", " ").title()

    @staticmethod
    def _make_radar_plot(per_hazard_df: pd.DataFrame, hazard_order: List[str], out_path: Path) -> None:
        """
        per_hazard_df columns: tool, hazard, pct_scenarios
        hazard_order: list of hazards (pretty) in order
        """
        tools = sorted(per_hazard_df["tool"].unique())
        N = len(hazard_order)
        if N < 3:
            # a radar chart with 2 axes is a bit silly: we do it anyway, but at least it won't crash
            pass

        angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
        angles += angles[:1]  # close the polygon

        fig = plt.figure()
        ax = plt.subplot(111, polar=True)

        for tool in tools:
            sub = per_hazard_df[per_hazard_df["tool"] == tool].set_index("hazard")
            values = [float(sub.loc[h, "pct_scenarios"]) if h in sub.index else 0.0 for h in hazard_order]
            values += values[:1]
            ax.plot(angles, values, linewidth=2, label=tool)
            ax.fill(angles, values, alpha=0.08)

        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(hazard_order, fontsize=9)
        ax.set_yticks([20, 40, 60, 80, 100])
        ax.set_ylim(0, 100)
        ax.set_title("% of scenarios with driving-style hazard (non-collision)")
        ax.legend(loc="upper right", bbox_to_anchor=(1.25, 1.15), fontsize=9)
        plt.tight_layout()
        plt.savefig(out_path, dpi=220)
        plt.close(fig)

    def run(self, output_dir: str) -> RQ4Result:
        out = Path(output_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)

        per_scenario_rows = []
        summary_rows = []
        per_hazard_rows = []

        # To keep a consistent order in the radar chart across different tools
        global_hazards_pretty: List[str] = []

        for tool, path in zip(self.tool_names, self.files):
            df = pd.read_csv(path)

            scenario_col = self._pick_scenario_col(df, self.scenario_id_col)
            driving_cols = self._pick_driving_p_cols(df, self.driving_hazards)

            # Count per scenario: how many driving-style hazards have P > threshold
            counts = (df[driving_cols] > self.p_threshold).sum(axis=1).astype(int)

            tmp = pd.DataFrame({
                "tool": tool,
                "scenario_id": df[scenario_col],
                "n_non_collision_driving_hazards": counts
            })
            per_scenario_rows.append(tmp)

            # Mean/std of the count
            summary_rows.append({
                "tool": tool,
                "n_scenarios": int(len(tmp)),
                "mean_non_collision_driving_hazards": float(counts.mean()),
                "std_non_collision_driving_hazards": float(counts.std(ddof=1)) if len(tmp) > 1 else 0.0,
                "p_threshold": self.p_threshold,
            })

            # % of scenarios per hazard: P_h > threshold
            for c in driving_cols:
                hazard_pretty = self._pretty_hazard(c)
                pct = 100.0 * float((df[c] > self.p_threshold).mean())
                per_hazard_rows.append({
                    "tool": tool,
                    "hazard": hazard_pretty,
                    "pct_scenarios": round(pct, 2)
                })
                global_hazards_pretty.append(hazard_pretty)

        # Save per-scenario
        per_scenario_df = pd.concat(per_scenario_rows, ignore_index=True)
        per_scenario_csv = out / "rq4_driving_style_non_collision_per_scenario.csv"
        per_scenario_df.to_csv(per_scenario_csv, index=False)

        # Save summary mean/std
        summary_df = pd.DataFrame(summary_rows).sort_values("tool")
        summary_csv = out / "rq4_driving_style_non_collision_summary.csv"
        summary_df.to_csv(summary_csv, index=False)

        # Save per-hazard % scenarios
        per_hazard_df = pd.DataFrame(per_hazard_rows).sort_values(["tool", "hazard"])
        per_hazard_csv = out / "rq4_driving_style_non_collision_per_hazard.csv"
        per_hazard_df.to_csv(per_hazard_csv, index=False)

        # Radar plot
        hazard_order = sorted(set(global_hazards_pretty))
        radar_path = out / "rq4_driving_style_non_collision_radar.png"
        self._make_radar_plot(per_hazard_df, hazard_order, radar_path)

        return RQ4Result(
            summary_csv_path=summary_csv,
            per_scenario_csv_path=per_scenario_csv,
            per_hazard_csv_path=per_hazard_csv,
            radar_plot_path=radar_path
        )