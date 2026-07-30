# SOTIF Pipeline

This document describes Stage 1 of the project: the SOTIF enrichment pipeline that turns a folder of base logs into a full SOTIF report. It is implemented by `SOTIFPipeline` (`src/pipeline/sotif_pipeline.py`) and driven by `src/pipeline/run_pipeline.py`.

Stage 1 is **generator-agnostic**: it doesn't matter whether the base logs under `outputs/<name>/` were produced by `src/runner/` (Stage 0, Scenic scenarios executed on CARLA) or by an externally-integrated tool (see `docs/integration.md`) - as long as the logs match the shape described in `docs/base_log_json.md`, the same pipeline applies.

## Running it

```bash
python -m src.pipeline.run_pipeline
```

This iterates every subfolder found under `outputs/` at the repository root and runs the steps below on each one, independently. A dataset folder is expected to contain `*_log_basic.json` files named `<scenario_id>_run_<NN>_log_basic.json`.

## Steps

For each dataset folder, `SOTIFPipeline.run()` executes, in order:

**STEP 0 - Sanity check.** Confirms at least one base log exists in the folder; aborts (for that dataset only) if not.

**STEP 0.5 - Critical/functional/dynamics metrics** (`src/data_gathering/enriching/orchestrator.py`). Computes, per run, from the raw frame-by-frame data: minimum distance before violation (MDBV), minimum time-to-collision (min TTC), time-exposed-TTC (TET), route-completion/stability metrics, and driving-dynamics statistics (mean/max speed, acceleration percentiles). Writes them back into each log's `results.critical_metrics` / `results.functional_metrics` / `results.dynamics_metrics`, in place. See `docs/enrichment.md` for details on these metrics.

**STEP A - ODD analysis** (`src/data_gathering/enriching/compute_sotif_odd.py`). Computes, per run, descriptive ODD sub-scores (`odd_env`/`odd_infra`/`odd_traffic`/`odd_operational`/`odd_global`), the concrete categorical value picked for each configured ODD factor (`sotif.odd_values`), and the list of triggering conditions that fired (`sotif.triggering_conditions`). Entirely driven by `config/sotif_odd_tc.yaml` - see `docs/enrichment.md`. Writes `odd_scores.csv` inside the dataset folder.

**STEP B - Hazard & severity** (`src/data_gathering/enriching/compute_sotif_hazard.py`). For each scenario (grouped across its runs), computes the empirical probability `P_h`, a fixed CARLA-Leaderboard-style severity `S_h`, and the residual risk `R_h = P_h * S_h` for 7 hazard categories: `collision_vehicle`, `collision_pedestrian`, `collision_static`, `red_light`, `stop_sign`, `off_road`, `lane_invasion`. A scenario is flagged `is_non_acceptable` if `R_h` exceeds the acceptance threshold (`hazards.acceptance_threshold` in `config/sotif_odd_tc.yaml`, default `0.2`) for at least one hazard. Writes `sotif_hazard_leaderboard.csv`.

**STEP C - Final SOTIF report** (`src/data_gathering/enriching/compute_sotif.py`). Aggregates, per scenario: hazard rates (`HR_*`), residual risk (`R_*`), their averages (`HR_avg`, `R_avg`), **average execution time** (`T_exec_avg`, from `results.total_simulation_time`, already computed per run by `CarlaBasicLogger.finalize_and_save()`), and route-completion rate. Writes `SOTIF_Final.csv`.

**STEP D - ODD/TC coverage and entropy** (`src/analysis/odd_tc_coverage.py`). Aggregates each scenario's ODD values/triggering conditions across its runs, then computes, per configured ODD factor and for triggering conditions as a whole: coverage (fraction of the declared taxonomy observed at least once) and normalized Shannon entropy (how evenly the suite is spread across the declared values). Computed twice: once over all scenarios, once restricted to `is_non_acceptable` ones. Writes `odd_tc_coverage_all.csv` and `odd_tc_coverage_non_acceptable.csv`.

## Output

Everything for one dataset ends up inside its own `outputs/<name>/` folder, alongside the (now-enriched) raw logs:

```text
outputs/<name>/
├── <scenario>_run_01_log_basic.json   (enriched in place by STEP 0.5 and STEP A)
├── ...
├── odd_scores.csv
├── sotif_hazard_leaderboard.csv
├── SOTIF_Final.csv
├── odd_tc_coverage_all.csv
└── odd_tc_coverage_non_acceptable.csv
```

## Extending the pipeline

Steps A-D all follow the same shape: a standalone script with an `argparse` CLI taking `--dataset_dir`, writing its output CSV directly inside `dataset_dir`, wired into `SOTIFPipeline` via `self._run_script(script, step_name, dataset_dir)` (a generic subprocess-invocation helper). A new step following that same `--dataset_dir` shape can be added the same way. STEP 0.5 is the one exception - `orchestrator.py` has a different CLI shape (`--input_dir`/`--output_dir`/`--workers`), so it's invoked through its own dedicated `compute_enriched_metrics()` method instead.
