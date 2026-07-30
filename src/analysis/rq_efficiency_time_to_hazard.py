"""
analysis/rq_efficiency_time_to_hazard.py

Efficiency = finding a hazard earlier (within a 60s run), using the timestamps in the `events` field.
Option B (run-level): mean/std computed only over the runs where the event occurs + hit-rate.

Hazard:
- collision (first collision, without filtering by category)
- lane_invasion, off_road, red_light, stop_sign
- driving_all = min(lane_invasion, off_road, red_light, stop_sign)
- driving_no_lane = min(off_road, red_light, stop_sign)

Input:
- logs_dir: directory containing *_log_basic.json files

Tool detection:
- prefers `tool` from the JSON
- otherwise uses the name of the file's parent folder (logs_dir/<TOOL>/*.json)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


@dataclass
class EfficiencyTTFResult:
    per_run_csv_path: Path
    per_hazard_csv_path: Path
    hit_rates_plot_path: Path
    mean_ttf_plot_path: Path
    boxplot_path: Path


class EfficiencyTimeToHazardAnalyzer:
    def __init__(
        self,
        logs_dir: str,
        output_dir: str,
        timeout_s: float = 60.0,
    ):
        self.logs_dir = Path(logs_dir).resolve()
        if not self.logs_dir.exists():
            raise FileNotFoundError(f"Log directory not found: {self.logs_dir}")
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_s = float(timeout_s)

        # hazard keys in the JSON events
        self.base_hazards = ["collision", "lane_invasion", "off_road", "red_light", "stop_sign"]

    def _discover_logs(self) -> List[Path]:
        files = sorted(self.logs_dir.rglob("*_log_basic.json"))
        if not files:
            raise FileNotFoundError(f"No '*_log_basic.json' found in {self.logs_dir}")
        return files

    @staticmethod
    def _safe_float(x: Any) -> Optional[float]:
        try:
            return float(x)
        except Exception:
            return None

    @staticmethod
    def _min_timestamp(events_list: Any, timeout_s: float) -> Optional[float]:
        if not isinstance(events_list, list) or len(events_list) == 0:
            return None
        ts = []
        for e in events_list:
            if isinstance(e, dict) and "timestamp" in e:
                try:
                    t = float(e["timestamp"])
                    if 0.0 <= t <= timeout_s:   # filter to reject bogus values (e.g. ~310s)
                        ts.append(t)
                except Exception:
                    pass
        return min(ts) if ts else None

    @staticmethod
    def _infer_tool(data: dict, file_path: Path) -> str:
        t = data.get("tool")
        if isinstance(t, str) and t.strip():
            return t.strip()
        # fallback: parent folder name
        return file_path.parent.name

    @staticmethod
    def _infer_scenario_run(file_name: str) -> (Optional[str], Optional[str]):
        # e.g.: scenario_04_run_02_log_basic.json
        # fallback best-effort
        import re
        m = re.search(r"(scenario_\d+).*?(run_\d+)", file_name)
        if m:
            return m.group(1), m.group(2)
        return None, None

    def run(self) -> EfficiencyTTFResult:
        rows = []

        for p in self._discover_logs():
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)

            events = data.get("events", {}) or {}
            tool = self._infer_tool(data, p)

            scenario_id = data.get("scenario_id") or data.get("scenario")
            run_index = data.get("run_index")

            # fallback from filename if missing
            scen_f, run_f = self._infer_scenario_run(p.name)
            if scenario_id is None:
                scenario_id = scen_f
            if run_index is None:
                run_index = run_f

            # timeout: use the value from the file if present, otherwise 60
            timeout = self.timeout_s
            results = data.get("results", {}) or {}
            if "total_simulation_time" in results:
                t = self._safe_float(results.get("total_simulation_time"))
                if t is not None and t > 0:
                    timeout = float(t)

            # TTF for each base hazard
            ttf: Dict[str, Optional[float]] = {}
            for h in self.base_hazards:
                ttf[h] = self._min_timestamp(events.get(h, []), timeout)

            # driving aggregates
            driving_all_list = [ttf["lane_invasion"], ttf["off_road"], ttf["red_light"], ttf["stop_sign"]]
            driving_no_lane_list = [ttf["off_road"], ttf["red_light"], ttf["stop_sign"]]

            def min_or_none(vals: List[Optional[float]]) -> Optional[float]:
                vv = [v for v in vals if v is not None]
                return min(vv) if vv else None

            ttf["driving_all"] = min_or_none(driving_all_list)
            ttf["driving_no_lane"] = min_or_none(driving_no_lane_list)

            row = {
                "tool": tool,
                "scenario_id": scenario_id,
                "run_index": run_index,
                "file": p.name,
                "timeout_s": timeout,
            }

            # save ttf and hit for each one
            hazards_out = ["collision", "lane_invasion", "off_road", "red_light", "stop_sign", "driving_all", "driving_no_lane"]
            for h in hazards_out:
                hit = 1 if ttf[h] is not None else 0
                row[f"hit_{h}"] = hit
                row[f"ttf_{h}_s"] = float(ttf[h]) if ttf[h] is not None else np.nan

            rows.append(row)

        df = pd.DataFrame(rows)

        # 1) per-run CSV
        per_run_csv = self.output_dir / "efficiency_per_run.csv"
        df.to_csv(per_run_csv, index=False)

        # 2) per-hazard summary (Option B)
        hazards = ["collision", "lane_invasion", "off_road", "red_light", "stop_sign", "driving_all", "driving_no_lane"]
        summary_rows = []

        for tool in sorted(df["tool"].unique()):
            sub = df[df["tool"] == tool]
            n_runs = len(sub)

            for h in hazards:
                hit_col = f"hit_{h}"
                ttf_col = f"ttf_{h}_s"

                n_hits = int(sub[hit_col].sum())
                hit_rate = 100.0 * (n_hits / n_runs) if n_runs > 0 else 0.0

                # Option B: mean/std computed only over the hits
                ttf_hits = sub.loc[sub[hit_col] == 1, ttf_col].astype(float).dropna()

                mean_ttf = float(ttf_hits.mean()) if len(ttf_hits) else np.nan
                std_ttf = float(ttf_hits.std(ddof=1)) if len(ttf_hits) > 1 else 0.0 if len(ttf_hits) == 1 else np.nan

                summary_rows.append({
                    "tool": tool,
                    "hazard": h,
                    "n_runs": int(n_runs),
                    "n_hits": int(n_hits),
                    "hit_rate_pct": round(hit_rate, 2),
                    "mean_ttf_s": round(mean_ttf, 3) if not np.isnan(mean_ttf) else np.nan,
                    "std_ttf_s": round(std_ttf, 3) if not np.isnan(std_ttf) else np.nan,
                })

        summary_df = pd.DataFrame(summary_rows)
        per_hazard_csv = self.output_dir / "efficiency_per_hazard_table.csv"
        summary_df.to_csv(per_hazard_csv, index=False)

        # ---- PLOTS ----
        # A) Hit-rates plot: per hazard, bars per tool
        hit_plot = self.output_dir / "efficiency_hit_rates.png"
        self._plot_grouped_bars(
            summary_df,
            value_col="hit_rate_pct",
            title="Efficiency: hit-rate (%) per hazard (higher = more frequent)",
            ylabel="hit_rate (%)",
            out_path=hit_plot
        )

        # B) Mean TTF plot: mean +/- std per hazard (hits only)
        mean_plot = self.output_dir / "efficiency_mean_ttf.png"
        self._plot_grouped_bars_with_error(
            summary_df,
            mean_col="mean_ttf_s",
            std_col="std_ttf_s",
            title="Efficiency: mean time-to-first-event (s) per hazard (lower = earlier) [hits only]",
            ylabel="mean TTF (s)",
            out_path=mean_plot
        )

        # C) Boxplot: collision vs driving_no_lane (hits only), per tool
        boxplot_path = self.output_dir / "efficiency_boxplot_ttf_collision_driving.png"
        self._plot_boxplots(df, boxplot_path)

        return EfficiencyTTFResult(
            per_run_csv_path=per_run_csv,
            per_hazard_csv_path=per_hazard_csv,
            hit_rates_plot_path=hit_plot,
            mean_ttf_plot_path=mean_plot,
            boxplot_path=boxplot_path
        )

    @staticmethod
    def _plot_grouped_bars(summary_df: pd.DataFrame, value_col: str, title: str, ylabel: str, out_path: Path) -> None:
        # pivot: rows hazard, cols tool
        pivot = summary_df.pivot(index="hazard", columns="tool", values=value_col)
        hazards = list(pivot.index)
        tools = list(pivot.columns)

        x = np.arange(len(hazards))
        width = 0.8 / max(1, len(tools))

        plt.figure(figsize=(max(9, 1 + len(hazards) * 1.0), 5))
        for i, tool in enumerate(tools):
            vals = pivot[tool].values.astype(float)
            plt.bar(x + i * width, vals, width, label=tool)

        plt.xticks(x + width * (len(tools) - 1) / 2, hazards, rotation=20, ha="right")
        plt.title(title)
        plt.ylabel(ylabel)
        plt.ylim(0, 100 if "rate" in value_col else None)
        plt.legend(fontsize=9)
        plt.tight_layout()
        plt.savefig(out_path, dpi=220)
        plt.close()

    @staticmethod
    def _plot_grouped_bars_with_error(summary_df: pd.DataFrame, mean_col: str, std_col: str, title: str, ylabel: str, out_path: Path) -> None:
        pivot_mean = summary_df.pivot(index="hazard", columns="tool", values=mean_col)
        pivot_std = summary_df.pivot(index="hazard", columns="tool", values=std_col)
        hazards = list(pivot_mean.index)
        tools = list(pivot_mean.columns)

        x = np.arange(len(hazards))
        width = 0.8 / max(1, len(tools))

        plt.figure(figsize=(max(9, 1 + len(hazards) * 1.0), 5))
        for i, tool in enumerate(tools):
            means = pivot_mean[tool].values.astype(float)
            stds = pivot_std[tool].values.astype(float)
            plt.bar(x + i * width, means, width, label=tool, yerr=stds, capsize=3)

        plt.xticks(x + width * (len(tools) - 1) / 2, hazards, rotation=20, ha="right")
        plt.title(title)
        plt.ylabel(ylabel)
        plt.legend(fontsize=9)
        plt.tight_layout()
        plt.savefig(out_path, dpi=220)
        plt.close()

    @staticmethod
    def _plot_boxplots(df: pd.DataFrame, out_path: Path) -> None:
        # two separate panels (aren't subplots needed here? we make two figures instead, to follow the "no subplots" rule)
        # Boxplot collision
        tools = sorted(df["tool"].unique())

        # collision
        plt.figure()
        data = []
        for t in tools:
            sub = df[(df["tool"] == t) & (df["hit_collision"] == 1)]["ttf_collision_s"].astype(float).dropna().values
            data.append(sub)
        plt.boxplot(data, labels=tools, showmeans=True)
        plt.title("TTF collision (hits only) by tool")
        plt.ylabel("seconds")
        plt.xticks(rotation=15, ha="right")
        plt.tight_layout()
        collision_path = out_path.parent / "efficiency_boxplot_ttf_collision.png"
        plt.savefig(collision_path, dpi=220)
        plt.close()

        # driving_no_lane
        plt.figure()
        data = []
        for t in tools:
            sub = df[(df["tool"] == t) & (df["hit_driving_no_lane"] == 1)]["ttf_driving_no_lane_s"].astype(float).dropna().values
            data.append(sub)
        plt.boxplot(data, labels=tools, showmeans=True)
        plt.title("TTF driving (no lane invasion) (hits only) by tool")
        plt.ylabel("seconds")
        plt.xticks(rotation=15, ha="right")
        plt.tight_layout()
        driving_path = out_path.parent / "efficiency_boxplot_ttf_driving_no_lane.png"
        plt.savefig(driving_path, dpi=220)
        plt.close()

        # save a "master" placeholder (so we always return a single path)
        # (not really necessary, but avoids breakage if a file is expected)
        out_path.write_text(
            "Generated:\n- efficiency_boxplot_ttf_collision.png\n- efficiency_boxplot_ttf_driving_no_lane.png\n",
            encoding="utf-8"
        )