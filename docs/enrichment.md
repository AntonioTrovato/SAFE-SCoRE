# Enrichment

Enrichment computes derived variables from the raw data of each execution log and writes them back into the log, in place. The reports are computed from them. It has two independent parts: **metrics** (step 0.5 of the evaluation) and **ODD and triggering-condition scoring** (step A). Distances are in meters, speeds in m/s, times in seconds.

## Numerical derived variables

`src/data_gathering/enriching/orchestrator.py` processes every log of a folder in parallel (one process per log). It first normalizes the log (`log_normalization.py`: event names, frame and timestamp conventions) and then runs three analyzers over the `frames` array. Results go to `results.critical_metrics`, `results.functional_metrics` and `results.dynamics_metrics`. Standalone:

```bash
python src/data_gathering/enriching/orchestrator.py --input_dir outputs/<name> --output_dir outputs/<name> --workers 8
```

### Critical metrics (`critical.py`)

For every frame and every other dynamic actor, the distance is computed between the 2D footprints (convex hull of the bounding-box vertices) of the ego and of the actor; it is 0 if they intersect. The time-to-collision of the pair is that distance divided by the magnitude of the relative velocity (2D), and infinite when the relative speed is practically 0. Frames without other actors are skipped.

| Field | Definition |
|---|---|
| `MDBV`, `MDBV_frame`, `MDBV_actor` | Smallest distance over the run, the frame where it occurs, and the actor (`id`, `type_id`) |
| `MDBV_per_actor` | Smallest distance per actor, sorted from the closest |
| `min_TTC`, `min_TTC_frame`, `min_TTC_actor` | Smallest time-to-collision over the run, its frame and actor |
| `TET_total` | Total time over which the smallest TTC of the frame is at or below 1.5 s (Time Exposed TTC) |
| `TET_max`, `TET_max_start_frame`, `TET_max_end_frame` | Longest continuous stretch below that threshold, and its first and last frame |

### Dynamics metrics (`dynamic.py`)

The ego velocity is projected on the vehicle axes (from its yaw). Speed is the magnitude of the longitudinal and lateral components; acceleration is the numerical derivative of the longitudinal velocity, in absolute value.

| Field | Definition |
|---|---|
| `mean_speed`, `max_speed` | Mean and maximum ego speed |
| `mean_long_acc`, `p95_long_acc`, `max_long_acc` | Mean, 95th percentile and maximum absolute longitudinal acceleration |

### Functional metrics (`functional.py`)

Route following: completion rate, distance travelled against planned distance, and deviation from the route. They need a route (`mission.waypoints` with at least two points) or a goal (`mission.end_location`) in the log. Logs written by SAFE-SCoRE's Runner carry neither, so these fields keep their default values (`completion_rate` 0, `is_completed_final` false, `completion_method` null) and no report uses them. They are meaningful for logs of tools that record a mission (`CarlaBasicLogger.set_mission_from_agent()`, see [integration.md](integration.md)).

## ODD and triggering-condition scoring

`src/data_gathering/enriching/compute_sotif_odd.py` evaluates, for each log, the ODD factors and triggering conditions declared in [`config/sotif_odd_tc.yaml`](../config/sotif_odd_tc.yaml) (loaded by `sotif_config.py`). It writes `sotif.odd_values` and `sotif.triggering_conditions` into the log, and `odd_scores.csv` into the dataset folder.

### Evaluation context

A condition or factor reads variables from a context with three roots:

- `world_state.*`: raw facts recorded by the Recorder at the start of the run: `map_name`, `cloudiness`, `precipitation`, `precipitation_deposits`, `wetness`, `wind_intensity`, `fog_density`, `sun_altitude_angle`, `num_npc_vehicles`, `num_pedestrians`, `ego_speed_limit_kmh`, `mission_timeout_s`.
- `derived.*`: categorical values computed from `world_state` by `compute_derived_fields()` (table below).
- `metrics.*`: numerical variables from the metrics above: `min_distance` (`MDBV`), `min_ttc` (`min_TTC`), `max_ego_speed` (`max_speed`), `avg_ego_speed` (`mean_speed`).

### Categorical derived variables

| `derived.` | Rule | Possible values |
|---|---|---|
| `weather_preset` | The reference weather closest (squared distance) to the run's `cloudiness`, `precipitation`, `precipitation_deposits`, `wetness`. References (in that order): ClearNoon (15, 0, 0, 0), CloudyNoon (80, 0, 0, 0), WetNoon (20, 0, 50, 50), SoftRainNoon (70, 15, 50, 60), HardRainNoon (100, 80, 90, 100) | `ClearNoon`, `CloudyNoon`, `WetNoon`, `SoftRainNoon`, `HardRainNoon` |
| `time_of_day` | `sun_altitude_angle` above 45: noon; above 15: morning; above -10: dusk; otherwise night | `noon`, `morning`, `dusk`, `night` |
| `road_condition` | The larger of `precipitation_deposits` and `wetness` above 30: wet; otherwise dry | `dry`, `wet` |
| `traffic_density` | `num_npc_vehicles` up to 2: low; up to 7: medium; otherwise high | `low`, `medium`, `high` |
| `pedestrian_density` | Same thresholds on `num_pedestrians` | `low`, `medium`, `high` |
| `speed_limit_bucket` | `ego_speed_limit_kmh` up to 50: low; up to 80: medium; otherwise high (50 if unknown) | `low`, `medium`, `high` |
| `timeout_bucket` | `mission_timeout_s` up to 20: short; up to 60: medium; otherwise long (30 if unknown) | `short`, `medium`, `long` |

The rules are generic heuristics, meant to be reviewed and adapted to the system under test. A declared ODD value that no rule produces (for example `afternoon`, `dawn`, `snow`, `ice` in the shipped configuration) can never be observed, so it can never be covered.

When a scenario does not set its weather, Scenic's CARLA model draws it uniformly from all CARLA weather presets; each draw is mapped to the nearest of the five references above.

### Factors and conditions

- **ODD factor** (`name`, `source`, `values`): the value of the factor in a run is the value of `source`, as a string (`unknown` if the source is missing). `values` is the universe of declared values for coverage and entropy.
- **Triggering condition** (`name`, `all_of`): fires when all predicates `{field, op, value}` hold. Operators: `lt`, `lte`, `gt`, `gte`, `eq`, `in`, `not_in`. A numerical comparison with a missing value is false.
- **Hazards**: severities and acceptance thresholds are read from the same file by step B ([sotif_pipeline.md](sotif_pipeline.md)).

To adapt the taxonomy to another system under test, edit the YAML and re-run the evaluation. A new categorical value needs one rule in `compute_derived_fields()`; a new raw fact needs one entry in `snapshot_world_state()` (`src/runner/recorder.py`) and is available in new executions only. Both procedures, and how to add a hazard, are in the README (Extending the configuration).
