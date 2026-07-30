import glob
import json
import os
from pathlib import Path
from typing import Dict, Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import umap
from kneed import KneeLocator
from scipy.spatial import ConvexHull
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.impute import SimpleImputer
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import RobustScaler


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def as_float(x, default=np.nan):
    """Safely converts a value to float."""
    try:
        if x is None:
            return default
        return float(x)
    except (ValueError, TypeError):
        return default


def scenario_has_collision_or_violation(log: Dict) -> bool:
    """Checks whether a scenario had collisions or violations."""
    res = log.get("results", {})
    if res.get("has_collision") or res.get("has_red_light_violation") or \
            res.get("has_speeding") or res.get("has_stop_violation"):
        return True
    ev = res.get("event_counts", {})
    return any((ev.get("collision", 0), ev.get("red_light", 0),
                ev.get("speeding", 0), ev.get("stop_sign", 0)))


def extract_features_from_log(log: Dict, file_path: str) -> Dict:
    """Extracts all raw features from a JSON log file."""
    res = log.get("results", {})
    crit = res.get("critical_metrics", {})
    func = res.get("functional_metrics", {}).get("performance", {})
    dev = func.get("deviation_stats", {})
    dyn = res.get("dynamics_metrics", {})
    ev = res.get("event_counts", {})

    features = {
        # Meta
        "tool": log.get("tool", "unknown"),
        "map_name": log.get("map_name", "unknown"),
        "generation_id": str(log.get("generation_id", "")),
        "scenario_id": str(log.get("scenario_id", "")),
        "start_time": str(log.get("start_time", "")),
        "file_path": file_path,
        # Critical
        "crit_MDBV": as_float(crit.get("MDBDA")),
        "crit_min_TTC": as_float(crit.get("min_TTC")),
        "crit_TET_total": as_float(crit.get("TET_total")),
        "crit_TET_max": as_float(crit.get("TET_max")),
        # Functional
        "func_completion_rate": as_float(func.get("completion_rate")),
        "func_route_stability": as_float(func.get("route_following_stability")),
        "func_time_to_completion": as_float(func.get("time_to_completion")),
        "func_total_planned_distance": as_float(func.get("total_planned_distance")),
        "func_actual_distance_traveled": as_float(func.get("actual_distance_traveled")),
        "func_max_progress_reached": as_float(func.get("max_progress_reached")),
        "func_dev_mean": as_float(dev.get("mean")),
        "func_dev_rmse": as_float(dev.get("rmse")),
        "func_dev_mae": as_float(dev.get("mae")),
        "func_dev_max": as_float(dev.get("max_deviation")),
        "func_dev_std": as_float(dev.get("std_dev")),
        # Dynamics
        "dyn_mean_speed": as_float(dyn.get("mean_speed")),
        "dyn_max_speed": as_float(dyn.get("max_speed")),
        "dyn_mean_long_acc": as_float(dyn.get("mean_long_acc")),
        "dyn_p95_long_acc": as_float(dyn.get("p95_long_acc")),
        "dyn_max_long_acc": as_float(dyn.get("max_long_acc")),
        # Events
        "ev_collision": int(ev.get("collision", 0)),
        "ev_red_light": int(ev.get("red_light", 0)),
        "ev_speeding": int(ev.get("speeding", 0)),
        "ev_stop_sign": int(ev.get("stop_sign", 0)),
        # Total failures (collision + violations)
        "total_failures": int(ev.get("collision", 0)) +
                          int(ev.get("red_light", 0)) +
                          int(ev.get("speeding", 0)) +
                          int(ev.get("stop_sign", 0)),
    }
    return features


def load_dataset(input_dir: Path):
    """Loads all logs, extracts features, and separates the critical data."""
    all_rows = []
    for file_path in glob.glob(str(input_dir / "**/*_log_basic.json"), recursive=True):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                log = json.load(f)

            row = extract_features_from_log(log, file_path)
            row["is_critical"] = scenario_has_collision_or_violation(log)
            all_rows.append(row)
        except Exception as e:
            print(f"Failed to load {file_path}: {e}")

    meta_df = pd.DataFrame(all_rows)
    df = meta_df[meta_df["is_critical"]].copy()  # Select only the critical scenarios
    return df, meta_df


# ============================================================================
# CLUSTERING FUNCTIONS
# ============================================================================

def evaluate_clustering(X: np.ndarray, k_min=3, k_max=10, random_state=42):
    """Computes WCSS (for the elbow method) and silhouette scores over a range of k."""
    wcss_scores = {}
    silhouette_scores = {}
    for k in range(k_min, k_max + 1):
        km = KMeans(
            n_clusters=k,
            n_init=10,
            random_state=random_state
        )
        labels = km.fit_predict(X)
        wcss_scores[k] = km.inertia_
        if len(set(labels)) > 1:
            silhouette_scores[k] = silhouette_score(X, labels)
        else:
            silhouette_scores[k] = -1
    return wcss_scores, silhouette_scores


def find_optimal_k(wcss_scores: Dict, silhouette_scores: Dict, k_min=3, k_max=10):
    """Finds the optimal k by combining the elbow method and silhouette analysis."""
    k_values = list(range(k_min, k_max + 1))
    wcss_values = [wcss_scores[k] for k in k_values]

    knee_locator = KneeLocator(
        k_values,
        wcss_values,
        curve="convex",
        direction="decreasing",
        online=True
    )
    elbow_k = knee_locator.elbow

    # find k with the best silhouette
    best_silhouette_k = max(silhouette_scores, key=lambda k: silhouette_scores[k])

    print(f"Elbow method suggests k={elbow_k}")
    print(f"Silhouette method suggests k={best_silhouette_k}")

    # selection strategy: prefer silhouette if reasonable, otherwise elbow
    if elbow_k == best_silhouette_k:
        optimal_k = best_silhouette_k
        selection_reason = "consensus"
    elif elbow_k:
        elbow_silhouette = silhouette_scores.get(elbow_k, -1)
        best_silhouette = silhouette_scores[best_silhouette_k]

        if best_silhouette - elbow_silhouette > 0.1:
            optimal_k = best_silhouette_k
            selection_reason = "silhouette_better"
        else:
            optimal_k = elbow_k
            selection_reason = "elbow_preferred"
    else:
        optimal_k = best_silhouette_k
        selection_reason = "silhouette_only"

    return optimal_k, elbow_k, best_silhouette_k, selection_reason


# ============================================================================
# VISUALIZATION FUNCTIONS
# ============================================================================

def plot_clustering_metrics(wcss_scores: Dict, silhouette_scores: Dict,
                            optimal_k: int, elbow_k: Optional[int], output_dir: Path):
    """Plots the Elbow and Silhouette charts for choosing k."""
    os.makedirs(output_dir.parent, exist_ok=True)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 5))
    k_values = sorted(wcss_scores.keys())

    # Elbow plot
    ax1.plot(k_values, [wcss_scores[k] for k in k_values],
             'bo-', linewidth=2, markersize=8)
    if elbow_k:
        ax1.axvline(x=elbow_k, color='red', linestyle='--',
                    alpha=0.7, label=f'Elbow k={elbow_k}')
    ax1.axvline(x=optimal_k, color='green', linestyle='-',
                alpha=0.7, label=f'Selected k={optimal_k}')
    ax1.set_title('Elbow Method for Optimal k')
    ax1.set_xlabel('Number of Clusters (k)')
    ax1.set_ylabel('WCSS')
    ax1.grid(True, alpha=0.3)
    ax1.legend()

    # Silhouette plot
    ax2.plot(k_values, [silhouette_scores[k] for k in k_values],
             'ro-', linewidth=2, markersize=8)
    ax2.axvline(x=optimal_k, color='green', linestyle='-',
                alpha=0.7, label=f'Selected k={optimal_k}')
    ax2.set_title('Silhouette Analysis for Optimal k')
    ax2.set_xlabel('Number of Clusters (k)')
    ax2.set_ylabel('Silhouette Score')
    ax2.grid(True, alpha=0.3)
    ax2.legend()

    plt.tight_layout()
    plt.savefig(output_dir.parent / "clustering_metrics_comparison.svg",
                dpi=300, bbox_inches="tight")
    plt.close()


def project_with_methods(X: np.ndarray, random_state: int = 42):
    projections = {}

    pca = PCA(n_components=2, random_state=random_state)
    projections["pca"] = {"data": pca.fit_transform(X), "model": pca}

    return projections


def get_cluster_centers_2d(X2: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """Computes the cluster centers in the projected 2D space."""
    unique_labels = np.unique(labels)
    centers = np.array([X2[labels == k].mean(axis=0) for k in unique_labels])
    return centers


def plot_clusters(X2, labels, centers2, out_path, title, silhouette):
    """Plots the clusters with convex hulls and centroids."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(12, 9))

    unique_labels = np.unique(labels[labels >= 0])
    n_clusters = len(unique_labels)

    if n_clusters <= 8:
        colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728',
                  '#9467bd', '#8c564b', '#e377c2', '#7f7f7f'][:n_clusters]
    else:
        colors = plt.cm.tab10(np.linspace(0, 1, n_clusters))

    # Plot each cluster
    for i, cluster_id in enumerate(unique_labels):
        cluster_points = X2[labels == cluster_id]

        ax.scatter(cluster_points[:, 0], cluster_points[:, 1],
                   s=50, c=colors[i], alpha=0.7,
                   edgecolors='white', linewidths=0.5,
                   label=f"Cluster {cluster_id}")

        # Draw convex hull boundary
        if len(cluster_points) >= 3:
            try:
                hull = ConvexHull(cluster_points)
                for simplex in hull.simplices:
                    ax.plot(cluster_points[simplex, 0],
                            cluster_points[simplex, 1],
                            color=colors[i], linewidth=2.0, alpha=0.8)
            except:
                pass

        # Add cluster annotation
        center = cluster_points.mean(axis=0)
        ax.annotate(f"C{cluster_id}\n(n={len(cluster_points)})",
                    center, xytext=(15, 15), textcoords='offset points',
                    ha="center", va="center", fontsize=8,
                    bbox=dict(boxstyle="round,pad=0.2",
                              facecolor="white", edgecolor="gray", alpha=0.9),
                    zorder=5)

    # Plot centroids
    if len(centers2) > 0:
        ax.scatter(centers2[:, 0], centers2[:, 1],
                   s=120, marker="*", c="black",
                   edgecolors="white", linewidth=1.0,
                   label="Centroids", zorder=15)

    ax.set_title(f"{title}\nSilhouette Coefficient: {silhouette:.4f}",
                 fontsize=16, fontweight='bold', pad=20)

    ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left",
              fontsize=11, frameon=True, fancybox=True, shadow=True)
    ax.grid(True, alpha=0.3, linestyle='--', linewidth=0.8)
    ax.set_facecolor('#FAFAFA')

    ax.tick_params(axis='both', which='major', labelsize=11)

    plt.tight_layout()
    plt.savefig(out_path, dpi=300, bbox_inches="tight",
                facecolor="white", edgecolor='none')
    plt.close()


def correlation_matrix(df: pd.DataFrame, features: list, out_path: Path):
    """Plots the feature correlation matrix."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    corr = df[features].corr()
    plt.figure(figsize=(12, 10))
    sns.heatmap(corr, annot=True, fmt=".2f", cmap="coolwarm",
                square=True, cbar_kws={"shrink": .8})
    plt.title("Correlation Matrix of Features", fontsize=16, fontweight='bold')
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    plt.close()


# ============================================================================
# CONFIGURATION
# ============================================================================

FEATURES_TO_CLUSTER = [
    # Critical
    "crit_min_TTC",
    "crit_TET_max",
    # Functional
    "func_total_planned_distance",
    "func_actual_distance_traveled",
    "func_dev_std",
    # Dynamics
    "dyn_mean_speed",
    "dyn_max_speed",
    "dyn_p95_long_acc",
    # Events
    "ev_collision",
    "ev_red_light",
    "ev_speeding",
    "ev_stop_sign",
    # Total failures
    "total_failures",
]

META_COLUMNS = ["tool", "map_name", "generation_id", "scenario_id",
                "start_time", "file_path"]


# ============================================================================
# MAIN EXECUTION
# ============================================================================

def run_clustering():
    # Load and prepare data
    #critical_df, full_df = load_dataset(Path("../../logs/enriched"))

    # Save datasets
    #full_df.to_csv("..datasets/full_dataset.csv", index=False)
    #critical_df.to_csv("..datasets/critical_dataset.csv", index=False)

    # Load critical dataset
    critical_df = pd.read_csv("../../datasets/critical_dataset.csv")

    # Select and scale features
    cols_to_use = [c for c in FEATURES_TO_CLUSTER if c in critical_df.columns]
    scaler = RobustScaler()
    X_std = scaler.fit_transform(
        SimpleImputer(strategy="median").fit_transform(critical_df[cols_to_use])
    )

    # UMAP dimensionality reduction
    umap_model = umap.UMAP(n_neighbors=15, min_dist=0.1,
                           n_components=10, random_state=42)
    X_reduced = umap_model.fit_transform(X_std)

    # Find optimal k
    wcss_scores, silhouette_scores = evaluate_clustering(
        X_reduced, k_min=2, k_max=10, random_state=42
    )
    optimal_k, elbow_k, best_silhouette_k, selection_reason = find_optimal_k(
        wcss_scores, silhouette_scores, k_min=2, k_max=10
    )
    plot_clustering_metrics(
        wcss_scores, silhouette_scores, optimal_k, elbow_k,
        Path("clustering_results/silhouette_vs_elbow.svg")
    )

    # Perform clustering
    kmeans = KMeans(
        n_clusters=optimal_k,
        n_init=10,
        random_state=42
    )
    labels = kmeans.fit_predict(X_reduced)

    # Visualize clusters
    projections = project_with_methods(X_reduced, 42)
    for proj_name, proj in projections.items():
        X2 = proj["data"]
        centers2 = get_cluster_centers_2d(X2, labels)
        plot_clusters(
            X2, labels, centers2,
            Path("../../clustering_results/plotting/clusters.svg"),
            "",
            silhouette=silhouette_scores[optimal_k]
        )

    # Save results
    results = {
        "optimal_k": int(optimal_k),
        "elbow_k": int(elbow_k) if elbow_k else None,
        "best_silhouette_k": int(best_silhouette_k) if best_silhouette_k else None,
        "silhouette_scores": {int(k): float(v) for k, v in silhouette_scores.items()},
        "wcss_scores": {int(k): float(v) for k, v in wcss_scores.items()},
        "optimal_silhouette": float(silhouette_scores[optimal_k]),
        "selection_reason": selection_reason
    }

    with open("../../clustering_results/clustering_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n[cluster] Clustering complete. Results in '{Path('clustering')}'.")
    print(f"-> optimal k: {optimal_k} (Silhouette: {silhouette_scores[optimal_k]:.3f})")

    # Merge clusters with full dataset
    full_dataset = pd.read_csv("../../datasets/full_dataset.csv")
    critical_with_cluster = critical_df.copy()
    critical_with_cluster["cluster"] = labels

    full_dataset = full_dataset.merge(
        critical_with_cluster[["file_path", "cluster"]],
        on="file_path",
        how="left"
    )

    full_dataset["cluster"] = full_dataset["cluster"].where(
        pd.notnull(full_dataset["cluster"]), None
    )

    full_dataset_path = Path("../../datasets/full_dataset_with_clusters.csv")
    #full_dataset.to_csv(full_dataset_path, index=False)

if __name__ == "__main__":
    run_clustering()