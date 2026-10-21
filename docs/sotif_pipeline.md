# SOTIF Pipeline

This document describes Stage 1 of the project: the SOTIF enrichment pipeline that turns a folder of base logs into a full SOTIF report. It is implemented by `SOTIFPipeline` (`src/pipeline/sotif_pipeline.py`) and driven by `src/pipeline/run_pipeline.py`.

Stage 1 is **generator-agnostic**: it doesn't matter whether the base logs under `outputs/<name>/` were produced by `src/runner/` (Stage 0, Scenic scenarios executed on CARLA) or by an externally-integrated tool (see `docs/integration.md`) - as long as the logs match the shape described in `docs/base_log_json.md`, the same pipeline applies.

## Running it

You normally never run this by hand: `run_experiment` runs it automatically on the folder it just produced. Run it yourself only to re-evaluate logs you already have — after editing `config/sotif_odd_tc.yaml`, or on logs from another tool.

```bash
# one dataset folder - the normal case
python -m src.pipeline.run_pipeline --output_folder my_run

# every folder under outputs/
python -m src.pipeline.run_pipeline --all
```

Exactly one of the two is required. With `--output_folder` only that folder is processed; with `--all` every subfolder of `outputs/` is processed independently, and an empty or foreign folder is skipped with a warning rather than aborting the rest. A dataset folder is expected to contain `*_log_basic.json` files named `<scenario_id>_run_<NN>_log_basic.json`.

## Steps

For each dataset folder, `SOTIFPipeline.run()` executes, in order:

**STEP 0 - Sanity check.** Confirms at least one base log exists in the folder; aborts (for that dataset only) if not.

**STEP 0.5 - Critical/functional/dynamics metrics** (`src/data_gathering/enriching/orchestrator.py`). Computes, per run, from the raw frame-by-frame data: minimum distance before violation (MDBV), minimum time-to-collision (min TTC), time-exposed-TTC (TET), route-completion/stability metrics, and driving-dynamics statistics (mean/max speed, acceleration percentiles). Writes them back into each log's `results.critical_metrics` / `results.functional_metrics` / `results.dynamics_metrics`, in place. See `docs/enrichment.md` for details on these metrics.

**STEP A - ODD analysis** (`src/data_gathering/enriching/compute_sotif_odd.py`). Computes, per run, the concrete categorical value picked for each configured ODD factor (`sotif.odd_values`), and the list of triggering conditions that fired (`sotif.triggering_conditions`). Entirely driven by `config/sotif_odd_tc.yaml` - see `docs/enrichment.md`. Writes `odd_scores.csv` inside the dataset folder.

**STEP B - Hazard & severity** (`src/data_gathering/enriching/compute_sotif_hazard.py`). For each scenario (grouped across its runs), computes the empirical probability `P_h`, a fixed CARLA-Leaderboard-style severity `S_h`, and the residual risk `R_h = P_h * S_h` for 7 hazard categories: `collision_vehicle`, `collision_pedestrian`, `collision_static`, `red_light`, `stop_sign`, `off_road`, `lane_invasion`. A scenario is flagged `is_non_acceptable` if, for at least one hazard, `R_h` exceeds that hazard's own acceptance threshold (`hazards.acceptance_threshold.<hazard>` in `config/sotif_odd_tc.yaml`, default `0.2` each). The CSV carries one `acceptance_threshold_<hazard>` column per hazard. Writes `final_hazard_report.csv`.

**STEP C - Final execution-time report** (`src/data_gathering/enriching/compute_execution_time_report.py`). For each scenario, computes the mean, median, minimum, maximum and 90th/95th/99th percentile (`T_exec_avg`, `T_exec_median`, `T_exec_min`, `T_exec_max`, `T_exec_p90`, `T_exec_p95`, `T_exec_p99`) of the wall-clock seconds of its executions, read from `results.total_wall_clock_time` of each log, which `CarlaBasicLogger.finalize_and_save()` computes (logs written before the rename call it `total_simulation_time` and are still read). Runs without a valid time are excluded. Hazard rates, risks and severities are not repeated here: they are in `final_hazard_report.csv`. Writes `final_execution_time_report.csv`.

**STEP D - ODD/TC coverage and entropy** (`src/analysis/odd_tc_coverage.py`). Counts, over the whole suite, how much of the declared ODD values and triggering conditions was exercised. A scenario counts towards every value and TC observed in any of its runs. Writes three reports: `odd_coverage_entropy_final_report.csv` (per ODD factor: declared and observed values, coverage, and normalized Shannon entropy, i.e. how evenly the scenarios are spread over the declared values), `odd_coverage_detail_final_report.csv` (per declared ODD value: number of scenarios and executions that exhibited it, 0 meaning not covered), and `tc_coverage_detail_final_report.csv` (per declared TC: same counts, plus a last `ALL_TCS` row with declared and observed TCs, coverage and entropy).


## Output

Everything for one dataset ends up inside its own `outputs/<name>/` folder, alongside the (now-enriched) raw logs:

```text
outputs/<name>/
├── <scenario>_run_01_log_basic.json   (enriched in place by STEP 0.5 and STEP A)
├── ...
├── odd_scores.csv
├── final_hazard_report.csv
├── final_execution_time_report.csv
├── odd_coverage_entropy_final_report.csv
├── odd_coverage_detail_final_report.csv
└── tc_coverage_detail_final_report.csv
```

## Extending the pipeline

Steps A-D all follow the same shape: a standalone script with an `argparse` CLI taking `--dataset_dir`, writing its output CSV directly inside `dataset_dir`, wired into `SOTIFPipeline` via `self._run_script(script, step_name, dataset_dir)` (a generic subprocess-invocation helper). A new step following that same `--dataset_dir` shape can be added the same way. STEP 0.5 is the one exception - `orchestrator.py` has a different CLI shape (`--input_dir`/`--output_dir`/`--workers`), so it's invoked through its own dedicated `compute_enriched_metrics()` method instead.
