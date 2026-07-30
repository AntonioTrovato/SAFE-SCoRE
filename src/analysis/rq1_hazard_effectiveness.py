"""
analysis/rq1_hazard_effectiveness.py

RQ1 (Registered Report):
"How effective are different scenario generation tools in producing hazardous scenarios?"

Output:
- Boxplot HR_avg per tool
- Boxplot R_avg per tool
- Heatmap of mean HR_<hazard> per tool
- CSV with summary statistics

Expected input (preferred):
- <ToolName>_metrics_SOTIF_Final.csv
  (typical columns: HR_* , R_* , HR_avg, R_avg, ...)

Supported fallback:
- <ToolName>_metrics_sotif_hazard_leaderboard.csv
  (typical columns: P_* and R_*; in this case HR_avg is computed from P_*)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from scipy import stats


@dataclass
class RQ1Result:
    merged_dataframe_path: Path
    summary_csv_path: Path
    hr_boxplot_path: Path
    r_boxplot_path: Path
    hazard_heatmap_path: Path
    report_txt_path: Path


class RQ1HazardEffectivenessAnalyzer:
    def __init__(self, csv_root_dir: Path):
        self.csv_root_dir = Path(csv_root_dir).resolve()

    # -------------------------
    # Discovery + loading
    # -------------------------
    def _discover_inputs(self) -> List[Tuple[str, Path]]:
        """
        Look for per-tool files in the folder.
        Preference order:
          1) *_SOTIF_Final.csv
          2) *_sotif_hazard_leaderboard.csv
        """
        candidates = []
        for p in sorted(self.csv_root_dir.glob("*.csv")):
            name = p.name

            # Try to extract the tool name from the pattern
            tool = None
            if name.endswith("_metrics_SOTIF_Final.csv"):
                tool = name.replace("_metrics_SOTIF_Final.csv", "")
            elif name.endswith("_metrics_sotif_hazard_leaderboard.csv"):
                tool = name.replace("_metrics_sotif_hazard_leaderboard.csv", "")

            if tool:
                candidates.append((tool, p))

        if not candidates:
            raise FileNotFoundError(
                f"No recognized CSV found in {self.csv_root_dir}. "
                f"Expected *_metrics_SOTIF_Final.csv or *_metrics_sotif_hazard_leaderboard.csv"
            )

        # If both Final and leaderboard exist for the same tool, keep Final
        best: Dict[str, Path] = {}
        for tool, path in candidates:
            if tool not in best:
                best[tool] = path
            else:
                # prefer SOTIF_Final
                if path.name.endswith("_metrics_SOTIF_Final.csv"):
                    best[tool] = path

        return sorted(best.items(), key=lambda x: x[0].lower())

    def _load_one(self, tool: str, path: Path) -> pd.DataFrame:
        df = pd.read_csv(path)
        df["tool"] = tool

        # Normalize: if HR_avg/R_avg are missing but P_* and R_* are present (leaderboard),
        # compute HR_avg as the mean of the P_* probabilities
        if "HR_avg" not in df.columns:
            p_cols = [c for c in df.columns if c.startswith("P_")]
            if p_cols:
                df["HR_avg"] = df[p_cols].mean(axis=1)
        if "R_avg" not in df.columns:
            r_cols = [c for c in df.columns if c.startswith("R_")]
            if r_cols:
                df["R_avg"] = df[r_cols].mean(axis=1)

        return df

    # -------------------------
    # Plots
    # -------------------------
    @staticmethod
    def _boxplot_by_tool(df: pd.DataFrame, y_col: str, title: str, out_path: Path) -> None:
        tools = list(df["tool"].unique())
        data = [df[df["tool"] == t][y_col].dropna().values for t in tools]

        plt.figure()
        plt.boxplot(data, labels=tools, showmeans=True)
        plt.ylabel(y_col)
        plt.title(title)
        plt.xticks(rotation=20, ha="right")
        plt.tight_layout()
        plt.savefig(out_path, dpi=200)
        plt.close()

    @staticmethod
    def _hazard_heatmap(df: pd.DataFrame, out_path: Path) -> None:
        # Prefer HR_* (SOTIF_Final). If not available, use P_*.
        hazard_cols = [c for c in df.columns if c.startswith("HR_") and c != "HR_avg"]
        value_kind = "HR"
        if not hazard_cols:
            hazard_cols = [c for c in df.columns if c.startswith("P_")]
            value_kind = "P"

        if not hazard_cols:
            raise RuntimeError("No hazard column found (neither HR_* nor P_*).")

        pivot = df.groupby("tool")[hazard_cols].mean()

        # Plot the heatmap "by hand" (matplotlib), no extra dependencies.
        plt.figure(figsize=(max(8, 1 + 0.8 * len(hazard_cols)), max(3.5, 1 + 0.6 * len(pivot))))
        im = plt.imshow(pivot.values, aspect="auto")
        plt.colorbar(im, fraction=0.046, pad=0.04)
        plt.yticks(range(pivot.shape[0]), pivot.index)
        plt.xticks(range(pivot.shape[1]), pivot.columns, rotation=35, ha="right")
        plt.title(f"Hazard heatmap (mean {value_kind} per hazard, per tool)")
        plt.tight_layout()
        plt.savefig(out_path, dpi=200)
        plt.close()

    # -------------------------
    # Stats + report
    # -------------------------
    @staticmethod
    def _kruskal_and_pairwise(df: pd.DataFrame, col: str) -> Tuple[Tuple[float, float], List[Tuple[str, str, float]]]:
        tools = list(df["tool"].unique())
        samples = [df[df["tool"] == t][col].dropna().values for t in tools]

        kw = stats.kruskal(*samples)
        pairwise = []
        m = (len(tools) * (len(tools) - 1)) // 2
        for i in range(len(tools)):
            for j in range(i + 1, len(tools)):
                a, b = tools[i], tools[j]
                u, p = stats.mannwhitneyu(
                    df[df["tool"] == a][col].dropna().values,
                    df[df["tool"] == b][col].dropna().values,
                    alternative="two-sided",
                )
                p_corr = min(1.0, p * m)  # Bonferroni
                pairwise.append((a, b, p_corr))

        return (float(kw.statistic), float(kw.pvalue)), pairwise

    @staticmethod
    def _summary_table(df: pd.DataFrame) -> pd.DataFrame:
        agg = df.groupby("tool")[["HR_avg", "R_avg"]].agg(["mean", "std", "median", "min", "max"])
        agg.columns = ["_".join(c).strip() for c in agg.columns.values]
        return agg.reset_index()

    # -------------------------
    # Main entry
    # -------------------------
    def run(self, output_dir: Path) -> RQ1Result:
        inputs = self._discover_inputs()

        frames = []
        for tool, path in inputs:
            frames.append(self._load_one(tool, path))

        df = pd.concat(frames, ignore_index=True)

        # Persist merged
        merged_path = output_dir / "rq1_merged.csv"
        df.to_csv(merged_path, index=False)

        # Plots
        hr_plot = output_dir / "rq1_hr_avg_boxplot.png"
        r_plot = output_dir / "rq1_r_avg_boxplot.png"
        heatmap_plot = output_dir / "rq1_hazard_heatmap.png"

        self._boxplot_by_tool(
            df=df,
            y_col="HR_avg",
            title="RQ1: distribution of mean hazard rate (HR_avg) by tool",
            out_path=hr_plot,
        )
        self._boxplot_by_tool(
            df=df,
            y_col="R_avg",
            title="RQ1: distribution of mean residual risk (R_avg) by tool",
            out_path=r_plot,
        )
        self._hazard_heatmap(df=df, out_path=heatmap_plot)

        # Summary CSV
        summary = self._summary_table(df)
        summary_path = output_dir / "rq1_summary.csv"
        summary.to_csv(summary_path, index=False)

        # Stats + text report
        (kw_hr_s, kw_hr_p), pw_hr = self._kruskal_and_pairwise(df, "HR_avg")
        (kw_r_s, kw_r_p), pw_r = self._kruskal_and_pairwise(df, "R_avg")

        report_lines = []
        report_lines.append("RQ1 - Hazard effectiveness report\n")
        report_lines.append("Summary (per tool):\n")
        report_lines.append(summary.to_string(index=False))
        report_lines.append("\n\nKruskal-Wallis (HR_avg): statistic={:.4f}, p={:.4e}".format(kw_hr_s, kw_hr_p))
        report_lines.append("Pairwise Mann-Whitney (Bonferroni-corrected p-values) for HR_avg:")
        for a, b, p in pw_hr:
            report_lines.append(f"  - {a} vs {b}: p_corr={p:.4e}")

        report_lines.append("\nKruskal-Wallis (R_avg): statistic={:.4f}, p={:.4e}".format(kw_r_s, kw_r_p))
        report_lines.append("Pairwise Mann-Whitney (Bonferroni-corrected p-values) for R_avg:")
        for a, b, p in pw_r:
            report_lines.append(f"  - {a} vs {b}: p_corr={p:.4e}")

        report_path = output_dir / "rq1_report.txt"
        report_path.write_text("\n".join(report_lines), encoding="utf-8")

        return RQ1Result(
            merged_dataframe_path=merged_path,
            summary_csv_path=summary_path,
            hr_boxplot_path=hr_plot,
            r_boxplot_path=r_plot,
            hazard_heatmap_path=heatmap_plot,
            report_txt_path=report_path,
        )