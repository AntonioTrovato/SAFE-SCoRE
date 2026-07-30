"""
umap_kmeans_rq3_fast.py
RQ3: UMAP + KMeans on scenario-level (merged) feature vectors.

Default:
- input:  datasets_dir/rq3_merged_feature_vectors_scenarios.csv
- output: datasets_dir/rq3_out/

Minimal usage:
python umap_kmeans_rq3_fast.py --datasets_dir datasets
"""

from __future__ import annotations

import argparse
from pathlib import Path
import numpy as np
import pandas as pd

from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

import matplotlib.pyplot as plt

try:
    import umap
except ImportError as e:
    raise SystemExit("Missing umap-learn. Install with: pip install umap-learn") from e


ID_COLS = {"scenario_id", "tool", "generation_id", "map_name"}


def pick_numeric_features(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in ID_COLS and pd.api.types.is_numeric_dtype(df[c])]


def drop_constant_columns(X: pd.DataFrame) -> pd.DataFrame:
    nunique = X.nunique(dropna=False)
    keep = nunique[nunique > 1].index.tolist()
    return X[keep]


def plot_umap(emb: np.ndarray, labels: pd.Series, title: str, out_path: Path):
    plt.figure()
    for v in labels.unique():
        m = (labels == v).to_numpy()
        plt.scatter(emb[m, 0], emb[m, 1], label=str(v), s=18)
    plt.title(title)
    plt.xlabel("UMAP-1")
    plt.ylabel("UMAP-2")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()


def plot_silhouette(df_sil: pd.DataFrame, out_path: Path):
    plt.figure()
    plt.plot(df_sil["k"], df_sil["silhouette"], marker="o")
    plt.title("Silhouette score vs K")
    plt.xlabel("K")
    plt.ylabel("Silhouette")
    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasets_dir", required=True, help="datasets/ folder")
    ap.add_argument("--in", dest="inp", default=None, help="Merged CSV (default: datasets_dir/rq3_merged_feature_vectors_scenarios.csv)")
    ap.add_argument("--out_dir", default=None, help="Output dir (default: datasets_dir/rq3_out)")
    ap.add_argument("--k_min", type=int, default=2)
    ap.add_argument("--k_max", type=int, default=10)
    ap.add_argument("--random_state", type=int, default=42)
    ap.add_argument("--fillna", type=float, default=0.0)
    ap.add_argument("--umap_neighbors", type=int, default=10)
    ap.add_argument("--umap_min_dist", type=float, default=0.1)
    args = ap.parse_args()

    datasets_dir = Path(args.datasets_dir)
    if not datasets_dir.exists():
        raise FileNotFoundError(f"datasets_dir not found: {datasets_dir}")

    inp = Path(args.inp) if args.inp else (datasets_dir / "rq3_merged_feature_vectors_scenarios.csv")
    out_dir = Path(args.out_dir) if args.out_dir else (datasets_dir / "rq3_out")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] Input merged: {inp.resolve()}", flush=True)
    print(f"[INFO] Output dir:  {out_dir.resolve()}", flush=True)

    df = pd.read_csv(inp)
    print(f"[INFO] Rows: {len(df)} | Columns: {len(df.columns)}", flush=True)

    num_cols = pick_numeric_features(df)
    if not num_cols:
        raise SystemExit("No numeric columns found for UMAP/KMeans.")

    X = df[num_cols].copy().fillna(args.fillna)

    before = X.shape[1]
    X = drop_constant_columns(X)
    after = X.shape[1]
    print(f"[INFO] Numeric features: {before} -> {after} after dropping constants", flush=True)
    if after < 2:
        raise SystemExit("Fewer than 2 features remain after dropping constants. UMAP would not make sense.")

    scaler = StandardScaler()
    Xs = scaler.fit_transform(X.values)

    print("[INFO] Computing UMAP...", flush=True)
    reducer = umap.UMAP(
        n_neighbors=args.umap_neighbors,
        min_dist=args.umap_min_dist,
        n_components=2,
        random_state=args.random_state,
    )
    emb = reducer.fit_transform(Xs)

    print("[INFO] Selecting K using silhouette...", flush=True)
    sil_rows = []
    best_k, best_sil, best_labels = None, -1.0, None

    k_min = max(2, args.k_min)
    k_max = max(k_min, args.k_max)

    for k in range(k_min, k_max + 1):
        km = KMeans(n_clusters=k, random_state=args.random_state, n_init="auto")
        labels = km.fit_predict(Xs)
        try:
            sil = silhouette_score(Xs, labels)
        except Exception:
            sil = float("nan")

        sil_rows.append({"k": k, "silhouette": sil})
        if np.isfinite(sil) and sil > best_sil:
            best_sil, best_k, best_labels = sil, k, labels

        print(f"[INFO] K={k} silhouette={sil}", flush=True)

    if best_k is None:
        raise SystemExit("No valid silhouette could be computed for the tested K values.")

    print(f"[DONE] Best K={best_k} silhouette={best_sil}", flush=True)

    df_sil = pd.DataFrame(sil_rows)
    df_sil.to_csv(out_dir / "rq3_kmeans_silhouette.csv", index=False)

    out_df = df.copy()
    out_df["umap_1"] = emb[:, 0]
    out_df["umap_2"] = emb[:, 1]
    out_df["cluster"] = best_labels
    out_df.to_csv(out_dir / "rq3_umap_embedding.csv", index=False)

    plot_umap(emb, out_df["tool"], "UMAP (color = tool)", out_dir / "rq3_umap_by_tool.png")
    plot_umap(emb, out_df["cluster"].astype(str), f"UMAP (color = cluster, K={best_k})", out_dir / "rq3_umap_by_cluster.png")
    plot_silhouette(df_sil, out_dir / "rq3_silhouette_by_k.png")

    print("[DONE] Output written to:", out_dir.resolve(), flush=True)


if __name__ == "__main__":
    main()
