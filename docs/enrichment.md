# Enrichment

"Enrichment" is the process of computing derived metrics from a base log's raw frame-by-frame data and writing them back into the log, in place, before the SOTIF report and coverage/entropy analyses consume them. It happens in two independent parts: **critical/functional/dynamics metrics** and **ODD/triggering-condition scoring**.

## Critical, functional, and dynamics metrics

Computed by `src/data_gathering/enriching/orchestrator.py`, which runs (in parallel, one process per log) three analyzers over each log's `frames` array:

- **`critical.py`** (`calculate_scenario_metrics`) - safety-criticality metrics:
  - `MDBV` / `MDBV_frame` / `MDBV_actor` / `MDBV_per_actor`: minimum distance between the ego's and another actor's bounding polygons over the run (Minimum Distance Before Violation), and which actor/frame it occurred at.
  - `min_TTC` / `min_TTC_frame` / `min_TTC_actor`: minimum time-to-collision estimate over the run.
  - `TET_total` / `TET_max`: total and longest-streak time the ego spent under a critical-TTC threshold (Time Exposed TTC).
- **`functional.py`** (`FunctionalAnalyzer`) - route-following metrics: completion rate, whether/when the route was completed (`is_completed_final`, `completion_frame`), distance traveled vs. planned, and path-following deviation statistics (mean/RMSE/MAE/max/std).
- **`dynamic.py`** (`DynamicsAnalyzer`) - driving-dynamics metrics: mean/max speed, mean/p95/max longitudinal acceleration.

Before any of this runs, `log_normalization.py` (`normalize_events`, `ensure_event_counts_schema`) reconciles naming/schema drift across differently-shaped input logs (event field names, timestamp/frame conventions) so the analyzers can assume a consistent shape.

Results are written into `results.critical_metrics`, `results.functional_metrics`, `results.dynamics_metrics` on each log. This is STEP 0.5 of the SOTIF pipeline (see `docs/sotif_pipeline.md`), run automatically; it can also be invoked standalone:

```bash
python src/data_gathering/enriching/orchestrator.py --input_dir outputs/<name> --output_dir outputs/<name> --workers 8
```

## ODD and triggering-condition scoring

Computed by `src/data_gathering/enriching/compute_sotif_odd.py`, entirely driven by `config/sotif_odd_tc.yaml` (loaded via `sotif_config.py`) rather than hardcoded assumptions, so the framework can be pointed at a different System Under Test's ODD without touching code.

**Inputs**, per log:
- `world_state`: raw facts snapshotted at scenario start (weather floats, actor counts, map name, ego speed limit, mission timeout/waypoints) - written by `src/runner/recorder.py` when logs are produced by Stage 0, or by an external integration following `docs/integration.md`.
- `results.critical_metrics` / `results.dynamics_metrics`: from the previous enrichment step.

**Processing**:
1. `compute_derived_fields()` turns raw `world_state` facts into categorical buckets (`weather_preset` via nearest-CARLA-preset matching, `time_of_day` from sun altitude, `road_condition` from precipitation/wetness, `traffic_density`/`pedestrian_density` from actor counts, and duration/speed/length buckets). This is a generic heuristic explicitly meant to be reviewed/tuned for a given SUT, not gospel.
2. Each ODD factor declared in the config (`odd_factors:` - name, category, source field, value→score map) is evaluated against these derived fields (or raw `world_state`/`metrics` fields directly), producing a score and the categorical value picked (`sotif.odd_values`). Per-category scores are averaged into `odd_env`/`odd_infra`/`odd_traffic`/`odd_operational`, and those four into `odd_global`.
3. Each triggering condition declared in the config (`triggering_conditions:` - name, `all_of` predicates over `world_state.*`/`derived.*`/`metrics.*` fields, using operators `lt`/`lte`/`gt`/`gte`/`eq`/`in`/`not_in`) is evaluated; the ones that fire are written to `sotif.triggering_conditions`.

**Editing the ODD/TC taxonomy** for a different SUT means editing `config/sotif_odd_tc.yaml` only - no Python changes required, as long as the new factors/conditions reference fields already present in `world_state`/`derived`/`metrics`. Adding a genuinely new *derived* field (e.g. a new bucket not yet computed) requires a small addition to `compute_derived_fields()`.

The acceptance threshold used by hazard/risk computation (`hazards.acceptance_threshold`, default `0.2`) also lives in this same config file.
