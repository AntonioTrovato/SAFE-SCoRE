# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

SAFE-SCoRE is a SOTIF-aligned (ISO 21448) evaluation framework for comparing automatic scenario generators used to validate ADAS/ADS in the CARLA simulator. `src/runner/run_experiment.py` executes a suite of Scenic (`.scenic`) scenarios directly on CARLA (`src/runner/`) and produces base-log JSON files; the rest of the pipeline (`src/data_gathering/`, `src/pipeline/`, `src/analysis/`) enriches those logs with SOTIF/ODD/hazard/behavioral metrics and runs comparative analyses across generators ("tools"). Logs can also come from an external scenario generator wired in independently (see `docs/integration.md`) — the enrichment/analysis stages don't care which path produced them, as long as the base-log JSON shape matches.

The stage separation is fundamental to the codebase: **scenario execution + logging** (`src/runner/` for Scenic/CARLA, or an externally-integrated generator using `src/data_gathering/carlaBasicLogger.py` directly) → **post-execution SOTIF enrichment and analysis** (`src/data_gathering/enriching/`, `src/pipeline/`, `src/analysis/`).

All Python source lives under `src/`, organized into packages: `src/runner`, `src/data_gathering` (+ `src/data_gathering/enriching`), `src/pipeline`, `src/analysis`, `src/utils`. `src/` itself is deliberately a plain folder, not a package (no `src/__init__.py`) — only the actual packages inside it have `__init__.py`. This means cross-package imports *inside* the code never use a `src.` prefix (e.g. `from utils.carla_help import ...`, `from data_gathering.carlaBasicLogger import ...`); the `src.` prefix only ever appears in the `python -m src.<pkg>.run_*` invocation used to run something from the repo root, since that's the path needed to locate the entry script from there. The three top-level entry scripts live inside their respective package (`src/pipeline/run_pipeline.py`, `src/analysis/run_analysis.py`, `src/runner/run_experiment.py`) rather than at the repo root. Non-code assets stay at the repo root: `config/` (the ODD/TC YAML), `outputs/`, `docs/`, `scenic_example/`.

## Environment & setup

- Target Python: `3.10` (required by the `scenic` dependency; earlier revisions of this project targeted `3.7.16`, which cannot run Scenic 3.x — don't assume 3.7 semantics).
- Setup:
  ```bash
  python3.10 -m venv venv
  source venv/bin/activate
  pip install -r requirements.txt
  pip install scenic  # not pinned in requirements.txt; see README.md
  ```
- CARLA (`carla==0.9.16`) is a real pip dependency (`requirements.txt`); there's no vendored CARLA client copy in this repo — a CARLA server must be running separately (locally, or the remote CARLA+Autoware Docker host passed via `--address`/`--port`).
- `src/utils/carla_help.py` reads a `CARLA_PATH` environment variable to locate a CARLA installation for helper utilities (starting/stopping `CarlaUE4`).
- Every entry script inserts both the repo root and `src/` onto `sys.path` at the top (`REPO_ROOT`/`SRC_ROOT` in each of the three `run_*.py` files) — `src/` so bare cross-package imports like `from pipeline.sotif_pipeline import ...` resolve, `REPO_ROOT` for anything path-based (locating `outputs/`, building subprocess script paths). This makes them work both via `python -m src.<pkg>.run_*` and via direct path invocation (`python src/<pkg>/run_*.py`) from the repo root.

## Running a scenario suite (`src/runner/run_experiment.py`)

```bash
python -m src.runner.run_experiment --input_dir scenic_example/common --output_folder scenic_demo --num_runs 10
```

Executes every `.scenic` file found (recursively) under `--input_dir`, `--num_runs` times each (SOTIF calls for repeated stochastic execution), then runs the SOTIF enrichment pipeline (below) automatically unless `--skip_enrichment` is passed. Two engines, selected via `--engine`:
- `behavior_agent` (default): connects to a local CARLA server; the ego is driven by whatever `behavior` the `.scenic` file itself compiles in (e.g. `EgoBehavior()`) — Scenic's own driving behaviors are the "agent" here, no separate CARLA `BehaviorAgent` is involved.
- `autoware`: only changes which CARLA server address/port (`--address`/`--port`) the runner connects to, on the assumption Autoware is already bridged to that server. This is a deliberate simplification — the actual Autoware bridge contract (who spawns/controls the ego) is out of scope for now.

Key mechanism (`src/runner/scenic_carla_runner.py` + `src/runner/recorder.py`): Scenic's `simulator.simulate()` owns the tick loop internally, so there's no external per-tick hook to call into `CarlaBasicLogger`. Instead, the runner builds a temp copy of each `.scenic` file (map path rewritten to an absolute path) with a small Scenic `monitor` appended, which calls `runner.recorder.on_monitor_step()` once per simulated step; that function lazily builds a `CarlaBasicLogger` + `ViolationMonitor` + collision/lane-invasion sensors on its first call (exactly what `docs/integration.md` describes for any generator) and calls `update_frame()` every step. **`src/data_gathering/carlaBasicLogger.py` and `violationMonitor.py` are reused completely unmodified** by this path.

The runner also snapshots a `world_state` block into each log (raw CARLA weather floats, actor counts, map name, ego speed limit, mission timeout) — this is what the config-driven ODD computation (below) reads instead of a generator-specific scenario-metadata dict.

## Running the enrichment/analysis pipelines

There is no test suite, linter config, or CI in this repo. The other two entry points:

```bash
python -m src.pipeline.run_pipeline    # SOTIF enrichment pipeline (per-dataset), same as run_experiment.py calls automatically
python -m src.analysis.run_analysis    # Cross-tool research-question (RQ) analyses
```

### SOTIF pipeline (`src/pipeline/run_pipeline.py` → `src/pipeline/sotif_pipeline.py`)

Expects an `outputs/` folder at the repo root, with one subfolder per scenario-generation tool/run (e.g. `outputs/SimADFuzz/`), each containing `*_log_basic.json` base logs. For every dataset subfolder, `SOTIFPipeline.run()` shells out (via `subprocess`) to these scripts in order:

0. `src/data_gathering/enriching/orchestrator.py` — critical/functional/dynamics metrics (TTC, MDBV, TET, completion/stability, dynamics), invoked with `--input_dir`/`--output_dir` both set to the dataset folder (in-place enrichment); this is the one step with a different CLI shape, so `SOTIFPipeline` calls it via its own dedicated `compute_enriched_metrics()` method rather than the generic `_run_script()` helper (below).
1. `src/data_gathering/enriching/compute_sotif_odd.py` — descriptive ODD scoring + triggering conditions, **driven by `config/sotif_odd_tc.yaml`** (see below) rather than hardcoded dicts → writes `odd_scores.csv` inside the dataset folder
2. `src/data_gathering/enriching/compute_sotif_hazard.py` — hazard/severity from event counts (expects filenames matching `<scenario_id>_run_<NN>_log_basic.json`), plus an `is_non_acceptable` column (any hazard's residual risk `R_h` exceeds `config/sotif_odd_tc.yaml`'s `hazards.acceptance_threshold`, default 0.2) → writes `sotif_hazard_leaderboard.csv`
3. `src/data_gathering/enriching/compute_sotif.py` — final per-dataset SOTIF report → writes `SOTIF_Final.csv`, including per-scenario **average execution time** (`T_exec_avg`, from `results.total_simulation_time`, already computed by `CarlaBasicLogger.finalize_and_save()`)
4. `src/analysis/odd_tc_coverage.py` — ODD/TC coverage & entropy against the config's declared taxonomy (see below), computed once over all scenarios and once restricted to `is_non_acceptable` ones → writes `odd_tc_coverage_all.csv` / `odd_tc_coverage_non_acceptable.csv`
5. (available but not wired into `.run()`) `src/data_gathering/enriching/compute_feature_vectors.py` — RQ3 feature vector extraction, invoked via `--outputs_dir`/`--out_dir` rather than `--dataset_dir`

Steps 1-4 all write their output CSV directly inside the dataset's own folder (no filename prefix needed, since each dataset already lives in its own directory) and are invoked uniformly as `python <script> --dataset_dir <path>` through `SOTIFPipeline._run_script()`. When adding a new step with that same `--dataset_dir` shape, follow the existing pattern: an `argparse` CLI taking `--dataset_dir`, callable standalone, output written inside `dataset_dir` itself, and wired into `SOTIFPipeline` as a `self._run_script(...)` call (script paths there are rooted at `base_dir / "src" / ...`).

### Config-driven ODD/TC (`config/sotif_odd_tc.yaml`)

The ODD factor taxonomy (name/category/source-field/value→score map) and triggering-condition rules (`all_of` predicates over `world_state.*`/`derived.*`/`metrics.*` fields) live entirely in this YAML — loaded via `src/data_gathering/enriching/sotif_config.py` (shared by `compute_sotif_odd.py`, `compute_sotif_hazard.py`, and `src/analysis/odd_tc_coverage.py`). To plug in a different SUT's ODD, edit this file — no Python changes needed. `compute_sotif_odd.py`'s `compute_derived_fields()` is the one place raw `world_state` facts get turned into the categorical buckets (`weather_preset`, `time_of_day`, `traffic_density`, etc.) the config's factors reference; it's a generic heuristic (e.g. nearest-CARLA-preset matching for weather) explicitly meant to be reviewed/tuned, not gospel.

`src/analysis/odd_tc_coverage.py` is distinct from `src/analysis/rq2_coverage_entropy.py`: the latter measures diversity over KMeans-*discovered* clusters of continuous dynamics/criticality metrics (still useful, untouched); the former measures coverage/entropy over the *named* ODD values/TC categories declared in the config, and reuses the same underlying risk/behavioral metrics (TTC, MDBV, dynamics, etc.) the rest of the project already computes — it isn't a parallel metric suite.

`src/data_gathering/enriching/orchestrator.py` computes `critical_metrics` (via `critical.py`), `functional_metrics` (via `functional.py`), and `dynamics_metrics` (via `dynamic.py`) in parallel across all logs under `--input_dir`; this is the step (STEP 0 in `SOTIFPipeline`, wired in automatically) that populates the metrics later consumed by the ODD/hazard/report/coverage scripts above. It can also be run directly against an arbitrary folder of logs:
```bash
python src/data_gathering/enriching/orchestrator.py --input_dir outputs --workers 8
```

### Analysis pipeline (`src/analysis/run_analysis.py` → `src/analysis/pipeline.py`)

`AnalysisPipeline` consumes the CSVs produced by the SOTIF pipeline (e.g. `*_SOTIF_Final.csv`, `*_sotif_hazard_leaderboard.csv`) and runs individual research-question analyzers, each independently invocable:
- `src/analysis/rq1_hazard_effectiveness.py` — hazard exposure effectiveness across tools
- `src/analysis/rq2_diversity.py`, `rq2_coverage_entropy.py`, `rq2_event_percentage.py` — hazardous-scenario diversity (UMAP+KMeans clustering, cluster-based coverage entropy, per-hazard event percentages)
- `src/analysis/rq4_driving_style_non_collision.py` — non-collision driving-style comparison
- `src/analysis/rq_efficiency_time_to_hazard.py` — time-to-first-hazard efficiency, reads directly from `*_log_basic.json` logs (not CSVs)

`run_analysis.py` currently exercises these mostly ad hoc with hardcoded paths and several lines commented out — treat it as a scratch driver/example, not a stable CLI, when extending it.

There is a second, largely redundant clustering/scoring/graphing path under `src/pipeline/` (`clustering.py`, `scores.py`, `graphs.py`, orchestrated by `src/pipeline/run_comparison.py`, plus RQ3-specific `src/pipeline/umap_kmeans_rq3.py` and `src/pipeline/merge_feature_vectors.py`). These operate on merged/global CSVs (e.g. `outputs/full_dataset_with_clusters.csv`) with relative paths assuming execution with the current working directory set to `src/pipeline/` itself (`../../outputs/...`, `../../results/...`), rather than on the per-dataset structure the `src/analysis/` package uses. Don't assume the two are interchangeable — check which one a given CSV/output path actually belongs to before wiring in new analysis.

## Log data model

The unit of data flowing through every stage is a base log JSON (see `docs/base_log_json.md` for a full annotated example), one per scenario execution, conventionally named `<generation_id>_<scenario_id>_log_basic.json` (or `<scenario_id>_run_<NN>_log_basic.json` for hazard-script compatibility) and produced by `src/data_gathering/carlaBasicLogger.py` (`CarlaBasicLogger`) + `src/data_gathering/violationMonitor.py` (`ViolationMonitor`). Key top-level structure:

- `tool`, `generation_id`, `scenario_id`, `map_name`, `run_index`, `delta_time` — run metadata
- `mission` — ego route/destination info
- `world_state` — raw facts snapshotted at scenario start by `src/runner/recorder.py` (weather floats, actor counts, map name, ego speed limit, mission timeout, waypoint count) — input to the config-driven ODD/TC computation
- `frames` — frame-by-frame ego + surrounding actor state (consumed by `critical.py`, `dynamic.py`, `functional.py` in enrichment)
- `results.event_counts` / `results.events` — collision, red-light, speeding, stop-sign, off-road, lane-invasion events
- `results.critical_metrics`, `results.functional_metrics`, `results.dynamics_metrics` — populated by `src/data_gathering/enriching/orchestrator.py`
- `results.total_simulation_time` / `results.simulated_time` — wall-clock vs. simulated execution time, computed automatically by `CarlaBasicLogger.finalize_and_save()`; `total_simulation_time` is what feeds the final report's `T_exec_avg`
- `sotif` — `odd_env`/`odd_infra`/`odd_traffic`/`odd_operational`/`odd_global` scores, `odd_values` (categorical value chosen per configured ODD factor), and `triggering_conditions`, all populated by `compute_sotif_odd.py`

`src/data_gathering/enriching/log_normalization.py` (`normalize_events`, `ensure_event_counts_schema`) is run first on every log to reconcile naming/schema drift before any metrics are computed — reuse it rather than reading raw event fields directly when writing new enrichment code.

## Integrating a new scenario-generation tool

Full walkthrough in `docs/integration.md`. Summary of the required wiring inside the generator's CARLA execution loop: instantiate `CarlaBasicLogger`, call `register_ego_actor` right after spawning the ego vehicle, register the ego in `LOGGER_REGISTRY[ego_vehicle.id]`, attach a `ViolationMonitor` and set it as `logger.violation_monitor`, forward collision sensor callbacks to `CarlaBasicLogger.handle_collision`, call `logger.update_frame(...)` once per simulation tick, and call `logger.finalize_and_save()` on every exit path (normal end, early stop, exception cleanup). Output logs should be written under `outputs/<ToolName>/` to match what the SOTIF pipeline expects. `src/runner/recorder.py` is a worked example of this same wiring, applied to Scenic-generated scenarios via a Scenic `monitor` instead of a hand-written simulation loop.
