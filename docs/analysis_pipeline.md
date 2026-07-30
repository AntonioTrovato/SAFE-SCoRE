# Analysis Pipeline

This document describes `src/analysis/`, which consumes the CSVs produced by the SOTIF pipeline (`docs/sotif_pipeline.md`) across one or more dataset folders and runs comparative, cross-tool research-question (RQ) analyses. This is a distinct, later stage from Stage 1 enrichment: Stage 1 produces per-dataset metrics; `src/analysis/` compares those metrics *across* datasets/tools.

## Entry point

```bash
python -m src.analysis.run_analysis
```

`run_analysis.py` is an **ad-hoc scratch driver**, not a stable CLI: most of the analyses inside it are commented out with example paths; uncomment and adjust the ones you need. It exercises `AnalysisPipeline` (`src/analysis/pipeline.py`), which wraps each analyzer below with a common `csv_root_dir` and `output_dir` convention.

## Research-question analyzers

- **`rq1_hazard_effectiveness.py`** - compares residual-risk/criticality scores across tools (boxplots, a heatmap, and a text summary), using the `*_SOTIF_Final.csv` / `*_sotif_hazard_leaderboard.csv` outputs of Stage 1.
- **`rq2_diversity.py`** - loads per-scenario feature vectors (numeric dynamics/criticality metrics, extracted via `compute_feature_vectors.py`) across tools, and clusters them with UMAP + KMeans to discover behavioral "shapes" of scenarios. This is unsupervised: it doesn't know about named ODD values or triggering conditions.
- **`rq2_coverage_entropy.py`** - given the clusters from `rq2_diversity.py`, computes per-tool coverage (fraction of discovered clusters touched) and normalized Shannon entropy (how evenly a tool's scenarios spread across those clusters). **This is different from** `src/analysis/odd_tc_coverage.py` (the Stage 1, STEP D module) - that one measures coverage/entropy over the *named* ODD values/TC categories declared in `config/sotif_odd_tc.yaml`, not over KMeans-discovered clusters. Both answer a "diversity" question, but over different axes.
- **`rq2_event_percentage.py`** - per-hazard event percentages across tools, from the hazard leaderboard CSVs.
- **`rq4_driving_style_non_collision.py`** - compares non-collision driving-style behavior (lane invasions, off-road, red-light, stop-sign) across tools, independent of raw collision rate.
- **`rq_efficiency_time_to_hazard.py`** - reads base logs directly (not CSVs) and measures time-to-first-hazard efficiency: how quickly, in simulated time, each tool's scenarios expose a hazard.

## A second, legacy path

`src/pipeline/` also contains a second, largely redundant clustering/scoring/graphing path (`clustering.py`, `scores.py`, `graphs.py`, orchestrated by `run_comparison.py`, plus RQ3-specific `umap_kmeans_rq3.py` and `merge_feature_vectors.py`). These operate on merged/global CSVs (e.g. `outputs/full_dataset_with_clusters.csv`) with relative paths that assume the current working directory is `src/pipeline/` itself, rather than on the per-dataset structure `src/analysis/` uses. The two paths are **not interchangeable** - check which one a given CSV/output path actually belongs to before extending either.
