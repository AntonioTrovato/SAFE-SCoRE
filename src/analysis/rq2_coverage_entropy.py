"""
analysis/rq3_coverage_entropy.py

RQ3 - Part 2:
Computation of Coverage and (Normalized) Entropy for each tool using the
clusters obtained from KMeans (K* already chosen).

Input:
- rq3_umap_embedding_<level>.csv (contains columns: tool, cluster, ...)

Output:
- rq3_coverage_entropy_<level>.csv
- rq3_coverage_entropy_<level>.png (simple bar chart)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


@dataclass
class RQ3Part2Result:
    metrics_csv_path: Path
    coverage_plot_path: Path
    entropy_plot_path: Path


class RQ3CoverageEntropyAnalyzer:
    def __init__(self, csv_root_dir: Path, level: str = "scenarios", min_count_per_cluster: int = 1):
        self.csv_root_dir = Path(csv_root_dir).resolve()
        self.level = level.lower().strip()
        if self.level not in {"scenarios", "runs"}:
            raise ValueError("level must be 'scenarios' or 'runs'")
        self.min_count_per_cluster = int(min_count_per_cluster)

    def _find_embedding_csv(self) -> Path:
        p = self.csv_root_dir / f"rq3_umap_embedding_{self.level}.csv"
        if not p.exists():
            raise FileNotFoundError(
                f"Could not find {p}. You must first run Part 1 (UMAP+KMeans) to generate the embedding."
            )
        return p

    @staticmethod
    def _shannon_entropy(p: np.ndarray) -> float:
        p = p[p > 0]
        return float(-(p * np.log(p)).sum())

    def run(self, output_dir: Path) -> RQ3Part2Result:
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        emb_csv = self._find_embedding_csv()
        df = pd.read_csv(emb_csv)

        if "tool" not in df.columns or "cluster" not in df.columns:
            raise RuntimeError("Embedding CSV must contain the 'tool' and 'cluster' columns.")

        # Total K
        clusters_all = sorted(df["cluster"].dropna().unique())
        K = len(clusters_all)
        if K < 2:
            raise RuntimeError(f"Number of clusters too low (K={K}).")

        rows = []
        for tool in sorted(df["tool"].astype(str).unique()):
            sub = df[df["tool"].astype(str) == tool]

            counts = (
                sub.groupby("cluster")
                .size()
                .reindex(clusters_all, fill_value=0)
                .to_numpy(dtype=int)
            )

            # Coverage: clusters with count >= threshold
            covered = int((counts >= self.min_count_per_cluster).sum())
            coverage = covered / K

            # Entropy over the distribution (always on the actual counts)
            total = counts.sum()
            if total == 0:
                entropy = 0.0
                entropy_norm = 0.0
            else:
                p = counts / total
                entropy = self._shannon_entropy(p)
                entropy_norm = entropy / np.log(K)

            rows.append(
                {
                    "tool": tool,
                    "K": K,
                    "total_points": int(total),
                    "covered_clusters": int(covered),
                    "coverage": float(coverage),
                    "entropy": float(entropy),
                    "entropy_norm": float(entropy_norm),
                    "min_count_per_cluster": self.min_count_per_cluster,
                }
            )

        out_df = pd.DataFrame(rows).sort_values("tool")
        out_csv = output_dir / f"rq3_coverage_entropy_{self.level}.csv"
        out_df.to_csv(out_csv, index=False)

        # Plot 1: Coverage
        coverage_plot = output_dir / f"rq3_coverage_{self.level}.png"
        plt.figure()
        plt.bar(out_df["tool"], out_df["coverage"])
        plt.ylim(0, 1.05)
        plt.title(f"RQ3: Coverage by tool [{self.level}]")
        plt.ylabel("coverage")
        plt.xticks(rotation=15, ha="right")
        plt.tight_layout()
        plt.savefig(coverage_plot, dpi=220)
        plt.close()

        # Plot 2: Normalized entropy
        entropy_plot = output_dir / f"rq3_entropy_norm_{self.level}.png"
        plt.figure()
        plt.bar(out_df["tool"], out_df["entropy_norm"])
        plt.ylim(0, 1.05)
        plt.title(f"RQ3: Normalized entropy by tool [{self.level}]")
        plt.ylabel("entropy_norm")
        plt.xticks(rotation=15, ha="right")
        plt.tight_layout()
        plt.savefig(entropy_plot, dpi=220)
        plt.close()

        return RQ3Part2Result(
            metrics_csv_path=out_csv,
            coverage_plot_path=coverage_plot,
            entropy_plot_path=entropy_plot
        )