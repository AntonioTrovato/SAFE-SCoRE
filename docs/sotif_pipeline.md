# SOTIF evaluation pipeline

The evaluation stage turns the base logs of `outputs/<name>/` into the SOTIF-aligned reports. It is implemented by `SOTIFPipeline` (`src/pipeline/sotif_pipeline.py`) and started by `src/pipeline/run_pipeline.py`.

It is independent of how the logs were produced: logs written by the Runner (`src/runner/`) or by an external tool (see [integration.md](integration.md)) are evaluated alike, as long as they have the shape of [base_log_json.md](base_log_json.md).

## Running it

`run_experiment` runs it automatically on the folder it just produced. Run it yourself only to re-evaluate existing logs, after editing `config/sotif_odd_tc.yaml` or for logs of another tool:

```bash
# one folder
python -m src.pipeline.run_pipeline --output_folder my_run

# every folder under outputs/
python -m src.pipeline.run_pipeline --all
```

Give exactly one of the two. Each folder is processed independently; a folder without base logs is skipped with a warning. Logs must be named `<scenario_id>_run_<NN>_log_basic.json`.

## Steps

For each folder, in order. Steps 0.5 to D run as subprocesses; if one fails, the pipeline stops with its error.

| Step | Script | What it does |
|---|---|---|
| 0 | `SOTIFPipeline.check_logs` | Checks that the folder has at least one `*_log_basic.json` |
| 0.5 | `data_gathering/enriching/orchestrator.py` | Computes critical, functional and dynamics metrics for each log and writes them into it ([enrichment.md](enrichment.md)) |
| A | `data_gathering/enriching/compute_sotif_odd.py` | Computes, for each log, the value of every ODD factor and the triggering conditions that fire; writes them into the log and into `odd_scores.csv` |
| B | `data_gathering/enriching/compute_sotif_hazard.py` | For each scenario, computes for each hazard `P` (fraction of its runs with the hazard counter above 0), `S` (from the configuration) and `R = P × S`; flags the scenario `is_non_acceptable` if any `R` exceeds its hazard's acceptance threshold; writes `final_hazard_report.csv` |
| C | `data_gathering/enriching/compute_execution_time_report.py` | For each scenario, computes mean, median, minimum, maximum, p90, p95 and p99 of `results.total_wall_clock_time`; runs without a valid time are excluded; writes `final_execution_time_report.csv` |
| D | `analysis/odd_tc_coverage.py` | Over the whole suite, computes coverage and normalized entropy of the declared ODD values and triggering conditions; writes `odd_coverage_entropy_final_report.csv`, `odd_coverage_detail_final_report.csv` and `tc_coverage_detail_final_report.csv` |

The columns of every report are described in the README (Output). The hazards, severities and thresholds come from `config/sotif_odd_tc.yaml`; the evaluation stops with an error if the two hazard mappings of that file do not list the same hazards.

## Output

```text
outputs/<name>/
├── <scenario>_run_01_log_basic.json   enriched in place by steps 0.5 and A
├── ...
├── odd_scores.csv
├── final_hazard_report.csv
├── final_execution_time_report.csv
├── odd_coverage_entropy_final_report.csv
├── odd_coverage_detail_final_report.csv
└── tc_coverage_detail_final_report.csv
```

## Adding a step

Steps A to D share one shape: a standalone script with an `argparse` CLI taking `--dataset_dir`, writing its output inside that folder, called from `SOTIFPipeline.run()` through `self._run_script(script, step_name, dataset_dir)`. A new step of this shape is added the same way. Step 0.5 is the exception: `orchestrator.py` takes `--input_dir`, `--output_dir` and `--workers`, and has its own method, `compute_enriched_metrics()`.
