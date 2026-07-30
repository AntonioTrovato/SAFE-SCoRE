"""
analysis/rq2_diversity.py

RQ2 - Diversity / Coverage (Part 1):
- Loading feature vectors (scenarios or runs) for each tool
- NaN imputation (median) + StandardScaler
- K* selection via Silhouette score on KMeans
- KMeans with K*
- UMAP 2D embedding for visualization
- UMAP plot: cluster points + per-tool perimeters (convex hull)
- CSV/PNG output
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

import umap
from scipy.spatial import ConvexHull


@dataclass
class RQ3Part1Result:
    merged_features_csv_path: Path
    embedding_csv_path: Path
    kmeans_summary_csv_path: Path
    silhouette_csv_path: Path
    silhouette_plot_path: Path
    umap_plot_path: Path
    umap_tool_hulls_plot_path: Path
    k_star: int


class RQ3DiversityUMAPKMeansAnalyzer:
    def __init__(
        self,
        csv_root_dir: Path,
        level: str = "scenarios",
        k_min: int = 2,
        k_max: int = 12,
        umap_n_neighbors: int = 25,
        umap_min_dist: float = 0.1,
        umap_metric: str = "euclidean",
        random_state: int = 42,

        force_k: int = 0,   # 0 = use silhouette, >0 = force K
    ):
        self.csv_root_dir = Path(csv_root_dir).resolve()
        self.level = level.lower().strip()
        if self.level not in {"scenarios", "runs"}:
            raise ValueError("level must be 'scenarios' or 'runs'")

        self.k_min = k_min
        self.k_max = k_max

        self.umap_n_neighbors = umap_n_neighbors
        self.umap_min_dist = umap_min_dist
        self.umap_metric = umap_metric
        self.random_state = random_state

        self.force_k = force_k

    # -------------------------
    # Input discovery
    # -------------------------
    def _discover_feature_files(self) -> List[Path]:
        pattern = f"*_metrics_feature_vectors_{self.level}.csv"
        files = sorted(self.csv_root_dir.glob(pattern))
        if not files:
            raise FileNotFoundError(
                f"No file found matching pattern '{pattern}' in {self.csv_root_dir}"
            )
        return files

    @staticmethod
    def _infer_feature_columns(df: pd.DataFrame) -> List[str]:
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()

        # discard columns that must not influence clustering
        drop_exact = {"run_index"}  # metadata, not a feature

        feature_cols = [c for c in numeric_cols if c not in drop_exact]
        if len(feature_cols) < 2:
            raise RuntimeError("At least 2 numeric features are needed for UMAP/KMeans.")
        return feature_cols

    # -------------------------
    # K selection via Silhouette
    # -------------------------
    def _choose_k_by_silhouette(self, Xs: np.ndarray, output_dir: Path) -> (int, Path, Path, Path):
        """
        Compute silhouette for k in [k_min, k_max] and select K*:
        - take the smallest k within 95% of the maximum silhouette (anti-spike).
        """
        k_min = max(2, int(self.k_min))
        k_max = int(self.k_max)

        # It doesn't make sense to have k >= n_samples
        n = Xs.shape[0]
        k_max = min(k_max, n - 1)
        if k_max < k_min:
            raise RuntimeError(f"Too few samples ({n}) to evaluate silhouette with k_min={k_min}.")

        rows = []
        best_k = None
        best_score = -1.0

        for k in range(k_min, k_max + 1):
            km = KMeans(n_clusters=k, random_state=self.random_state, n_init=10)
            labels = km.fit_predict(Xs)

            # Silhouette requires >=2 clusters and that they are not all identical
            score = silhouette_score(Xs, labels, metric="euclidean")
            rows.append({"k": k, "silhouette": float(score)})

            if score > best_score:
                best_score = score
                best_k = k

        sil_df = pd.DataFrame(rows)

        # anti-spike: choose the smallest k within 95% of the max
        threshold = 0.95 * best_score
        k_star = int(sil_df[sil_df["silhouette"] >= threshold]["k"].min())

        sil_csv = output_dir / f"rq3_silhouette_{self.level}.csv"
        sil_df.to_csv(sil_csv, index=False)

        sil_png = output_dir / f"rq3_silhouette_{self.level}.png"
        plt.figure()
        plt.plot(sil_df["k"], sil_df["silhouette"], marker="o")
        plt.title(f"RQ3: Silhouette score vs k (K*={k_star}) [{self.level}]")
        plt.xlabel("k (n_clusters)")
        plt.ylabel("silhouette")
        plt.tight_layout()
        plt.savefig(sil_png, dpi=220)
        plt.close()

        return k_star, sil_df, sil_csv, sil_png

    # -------------------------
    # Geometry: convex hull
    # -------------------------
    @staticmethod
    def _convex_hull_polygon(points: np.ndarray) -> Optional[np.ndarray]:
        if points.shape[0] < 3:
            return None
        try:
            hull = ConvexHull(points)
            return points[hull.vertices]
        except Exception:
            return None

    # -------------------------
    # Run
    # -------------------------
    def run(self, output_dir: Path) -> RQ3Part1Result:
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        # Load and merge all tools
        files = self._discover_feature_files()
        frames = []
        for f in files:
            df = pd.read_csv(f)
            if "tool" not in df.columns:
                raise RuntimeError(f"Missing 'tool' column in {f.name}")
            frames.append(df)

        df_all = pd.concat(frames, ignore_index=True)

        merged_path = output_dir / f"rq3_merged_features_{self.level}.csv"
        df_all.to_csv(merged_path, index=False)

        # Tool labels (needed for the hull)
        tools = df_all["tool"].astype(str).to_numpy()

        # Features
        feature_cols = self._infer_feature_columns(df_all)
        X = df_all[feature_cols].to_numpy(dtype=float)

        # 1) inf -> NaN
        X = np.where(np.isfinite(X), X, np.nan)

        # 2) median imputation
        imputer = SimpleImputer(strategy="median")
        Xi = imputer.fit_transform(X)

        # 3) standardization
        scaler = StandardScaler()
        Xs = scaler.fit_transform(Xi)

        # 4) choose K* via silhouette
        # k_star, sil_df, sil_csv, sil_png = self._choose_k_by_silhouette(Xs, output_dir)
        # 4) choose K
        if getattr(self, "force_k", 0) and self.force_k > 1:
            k_star = int(self.force_k)
            sil_csv = output_dir / f"rq3_silhouette_{self.level}.csv"
            sil_png = output_dir / f"rq3_silhouette_{self.level}.png"
            # optional: skip generating silhouette if K is forced, or generate it anyway
        else:
            k_star, sil_df, sil_csv, sil_png = self._choose_k_by_silhouette(Xs, output_dir)

        # 5) final KMeans with K*
        km = KMeans(n_clusters=k_star, random_state=self.random_state, n_init=10)
        clusters = km.fit_predict(Xs)

        # 6) UMAP (visualization only)
        reducer = umap.UMAP(
            n_neighbors=self.umap_n_neighbors,
            min_dist=self.umap_min_dist,
            metric=self.umap_metric,
            random_state=self.random_state,
        )
        emb = reducer.fit_transform(Xs)  # Nx2

        # Embedding CSV
        emb_df = df_all.copy()
        emb_df["umap_x"] = emb[:, 0]
        emb_df["umap_y"] = emb[:, 1]
        emb_df["cluster"] = clusters

        emb_csv = output_dir / f"rq3_umap_embedding_{self.level}.csv"
        emb_df.to_csv(emb_csv, index=False)

        # Summary: cluster counts per tool (+ percentages)
        summary = (
            emb_df.groupby(["tool", "cluster"])
            .size()
            .reset_index(name="count")
            .sort_values(["tool", "cluster"])
        )
        totals = emb_df.groupby("tool").size().reset_index(name="total")
        summary = summary.merge(totals, on="tool", how="left")
        summary["pct"] = summary["count"] / summary["total"]

        summary_csv = output_dir / f"rq3_kmeans_summary_{self.level}.csv"
        summary.to_csv(summary_csv, index=False)

        # Plot 1: points colored by cluster
        umap_plot = output_dir / f"rq3_umap_kmeans_points_{self.level}.png"
        plt.figure()
        for c in sorted(np.unique(clusters)):
            mask = clusters == c
            plt.scatter(emb[mask, 0], emb[mask, 1], s=18, label=f"cluster {c}", alpha=0.85)
        plt.title(f"RQ3: UMAP (2D) colored by KMeans clusters (K*={k_star}) [{self.level}]")
        plt.xlabel("UMAP-1")
        plt.ylabel("UMAP-2")
        plt.legend(markerscale=1.2, fontsize=9)
        plt.tight_layout()
        plt.savefig(umap_plot, dpi=220)
        plt.close()

        # Plot 2: perimeters per tool
        hull_plot = output_dir / f"rq3_umap_tool_hulls_{self.level}.png"
        plt.figure()
        plt.scatter(emb[:, 0], emb[:, 1], s=12, alpha=0.55)

        for tool in sorted(np.unique(tools)):
            mask = tools == tool
            pts = emb[mask]
            poly = self._convex_hull_polygon(pts)
            if poly is None:
                center = pts.mean(axis=0)
                plt.scatter(center[0], center[1], s=70, marker="x", label=f"{tool} (center)")
                continue
            poly_closed = np.vstack([poly, poly[0]])
            plt.plot(poly_closed[:, 0], poly_closed[:, 1], linewidth=2, label=f"{tool} hull")

        plt.title(f"RQ3: UMAP with tool perimeters (convex hull) (K*={k_star}) [{self.level}]")
        plt.xlabel("UMAP-1")
        plt.ylabel("UMAP-2")
        plt.legend(fontsize=9)
        plt.tight_layout()
        plt.savefig(hull_plot, dpi=220)
        plt.close()

        return RQ3Part1Result(
            merged_features_csv_path=merged_path,
            embedding_csv_path=emb_csv,
            kmeans_summary_csv_path=summary_csv,
            silhouette_csv_path=sil_csv,
            silhouette_plot_path=sil_png,
            umap_plot_path=umap_plot,
            umap_tool_hulls_plot_path=hull_plot,
            k_star=k_star,
        )