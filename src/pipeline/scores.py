import json

import numpy as np
import pandas as pd
from scipy.stats import entropy

def normalize_ts(x, max_val=120.0):
    """Normalizes the completion time (TS) with respect to a maximum value."""
    if x is None or np.isnan(x) or x <= 0:
        return 0.0
    return min(1.0, x / max_val)


def compute_scores():
    """
    Computes the C(T), D(T), E(T) scores for each tool.
    """

    # load dataset
    df = pd.read_csv("../../datasets/full_dataset_with_clusters.csv")

    tools = list(df["tool"].unique())
    results = {}

    # compute metrics and scores for each tool
    for tool in tools:
        df_tool = df[df["tool"] == tool]
        total_scen = len(df_tool)

        # --- Collision and violation ---
        scen_with_collision = sum(df_tool["ev_collision"] > 0)
        scen_with_violation = sum(
            (df_tool["ev_red_light"] > 0) |
            (df_tool["ev_stop_sign"] > 0) |
            (df_tool["ev_speeding"] > 0)
        )
        collision_rate = scen_with_collision / total_scen if total_scen > 0 else 0.0
        violation_rate = scen_with_violation / total_scen if total_scen > 0 else 0.0



        # --- Completion and Route Following (already normalized to [0,1]) ---
        comp_rate = float(np.nanmean(df_tool["func_completion_rate"] / 100.0))
        rfs_mean = float(np.nanmean(df_tool["func_route_stability"] / 100.0))

        # --- Time Spent (normalized with max=120s) ---
        tc_values = df_tool["func_time_to_completion"].dropna()
        tc_mean = tc_values.mean() if len(tc_values) > 0 else 0.0
        ts_norm = normalize_ts(tc_mean, 120.0)

        # --- Near-miss metrics: computed only on scenarios without collisions ---
        df_nearmiss = df_tool[df_tool["ev_collision"] == 0]

        ttc_mean = float(np.nanmean(df_nearmiss["crit_min_TTC"])) if len(df_nearmiss) > 0 else None
        mdbv_mean = float(np.nanmean(df_nearmiss["crit_MDBV"])) if len(df_nearmiss) > 0 else None
        tet_mean = float(np.nanmean(df_nearmiss["crit_TET_total"])) if len(df_nearmiss) > 0 else None

        # --- Criticality score C(T) ---
        C_raw = (0.50 * collision_rate +
                 0.30 * violation_rate +
                 0.10 * (1.0 - comp_rate) +
                 0.05 * (1.0 - rfs_mean) +
                 0.05 * ts_norm)
        C = max(0.0, min(1.0, C_raw))

        # --- Diversity score D(T) ---
        clustered = df_tool.dropna(subset=["cluster"])
        num_clusters_total = df["cluster"].nunique()
        num_clusters_tool = clustered["cluster"].nunique()
        cov = num_clusters_tool / num_clusters_total if num_clusters_total > 0 else 0.0

        counts = clustered["cluster"].value_counts().values
        if counts.sum() > 0 and num_clusters_total > 1:
            probability = counts / counts.sum()
            cent = entropy(probability, base=2) / np.log2(num_clusters_total)
        else:
            cent = 0.0
        D = 0.5 * cov + 0.5 * cent

        # --- Effectiveness score E(T) ---
        critical_scen = scen_with_collision + scen_with_violation
        E = critical_scen / total_scen if total_scen > 0 else 0.0

        # --- Save results ---
        results[tool] = {
            # Basic statistics
            "total_scenarios": total_scen,
            "num_scen_with_collision": int(scen_with_collision),
            "num_scen_with_violation": int(scen_with_violation),
            "cluster_count": int(num_clusters_tool),

            "Criticity": {
                "CollisionRate": collision_rate,
                "ViolationRate": violation_rate,
                "CompletionRate": comp_rate,
                "RFS": rfs_mean,
                "TS": ts_norm,
                "C": C,
                "LatentFactors": {
                    "TTC": ttc_mean,
                    "MDBV": mdbv_mean,
                    "TET": tet_mean,
                },
            },
            "Diversity": {
                "Coverage": cov,
                "Entropy": cent,
                "D": D,
            },
            "Effectiveness": {
                "CriticalScenarios": critical_scen,
                "E": E,
            },
        }

    # --- 3. SAVE RESULTS ---
    out_path = "../../results/tool_scores.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"[scores] Saved scores to {out_path}")

if __name__ == "__main__":
    compute_scores()

