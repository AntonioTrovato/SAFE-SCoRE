# Execution log schema

One JSON file per scenario execution, named `<scenario_id>_run_<NN>_log_basic.json`. The Recorder (`CarlaBasicLogger`, `ViolationMonitor`) writes it; the evaluation adds the metrics and the `sotif` block in place. Units: meters, seconds, m/s, degrees for rotations. Floats are rounded to 4 decimals.

The excerpt below comes from `outputs/demo_run/common20_1_run_01_log_basic.json`, shortened: lists are cut to one element and marked `...`.

```json
{
  "tool": "demo_run",
  "generation_id": "autoware",
  "scenario_id": "common20_1",
  "map_name": "Town05",
  "run_index": 1,
  "start_time": "2026-10-22_12-23-27",
  "wall_clock_start_epoch": 1792664607.1446,
  "delta_time": 0.05,

  "world_state": {
    "map_name": "Town05",
    "cloudiness": 20.0, "precipitation": 30.0, "precipitation_deposits": 50.0,
    "wetness": 0.0, "wind_intensity": 30.0, "fog_density": 2.0,
    "sun_altitude_angle": 15.0,
    "num_npc_vehicles": 2, "num_pedestrians": 0,
    "ego_speed_limit_kmh": 30.0, "mission_timeout_s": 20.0
  },

  "results": {
    "has_collision": true,
    "has_red_light_violation": true,
    "has_speeding": false,
    "has_stop_violation": false,
    "event_counts": {
      "collision": 1, "collision_vehicle": 1, "collision_pedestrian": 0, "collision_static": 0,
      "red_light": 1, "stop_sign": 0, "speeding": 0, "lane_invasion": 0, "off_road": 0
    },
    "total_frames": 380,
    "total_wall_clock_time": 33.2,
    "simulated_time": 18.9,

    "critical_metrics": {
      "MDBV": 0.0, "MDBV_frame": 105, "MDBV_actor": {"id": "143", "type_id": "vehicle.lincoln.mkz_2017"},
      "min_TTC": 0.0, "min_TTC_frame": 105, "min_TTC_actor": {"id": "143", "type_id": "vehicle.lincoln.mkz_2017"},
      "TET_total": 5.4, "TET_max": 3.4, "TET_max_start_frame": 141, "TET_max_end_frame": 208,
      "MDBV_per_actor": [{"actor_id": "143", "type_id": "vehicle.lincoln.mkz_2017", "min_distance": 0.0, "frame": 105}, "..."]
    },
    "dynamics_metrics": {
      "mean_speed": 1.67, "max_speed": 4.90,
      "mean_long_acc": 1.36, "p95_long_acc": 2.13, "max_long_acc": 41.4
    },
    "functional_metrics": {"performance": {"completion_rate": 0.0, "waypoint_alignment_ok": false, "...": "..."}},
    "events": { "...": "copy of the top-level events" }
  },

  "events": {
    "collision": [{
      "frame": 107, "timestamp": 5.3,
      "actors_involved": {
        "ego": {"type_id": "vehicle.toyota.prius", "id": 138, "location": [36.3, -71.7, 0.04], "speed_mps": 1.59},
        "other_actor": {"type_id": "vehicle.lincoln.mkz_2017", "id": 143, "location": [34.9, -67.3, 0.03], "speed_mps": 5.02}
      },
      "impact": {
        "impulse": {"vector": [-1875.9, 2018.7, -180.0], "magnitude": 2761.6},
        "impact_side": "Retro", "impact_angle_deg": -137.6, "classification_method": "impulse"
      },
      "collision_category": "collision_vehicle",
      "other_actor_id": 143, "other_actor_type_id": "vehicle.lincoln.mkz_2017"
    }],
    "red_light":    [{"frame": 128, "timestamp": 6.4, "speed_kmh": 4.9, "carla_frame": 5155}],
    "speeding": [], "stop_sign": [], "lane_invasion": [], "off_road": []
  },

  "mission": {"start_location": null, "end_location": null, "waypoints": []},

  "actors": {
    "138": {"type_id": "vehicle.toyota.prius", "role": "ego", "spawn_frame": 0,
            "spawn_carla_frame": 5027, "spawn_carla_timestamp": 57.96,
            "spawn_transform": [36.29, -69.40, 0.04, -0.01, 0.00, -88.47]},
    "143": {"type_id": "vehicle.lincoln.mkz_2017", "role": "npc", "spawn_frame": 379, "...": "..."}
  },

  "frames": [{
    "frame": 100, "carla_frame": 5127, "timestamp": 5.0, "delta_time": 0.05,
    "ego_vehicle": {
      "location": [36.30, -71.25, 0.04], "rotation": [-0.0, 0.01, -89.42],
      "velocity": [-0.00, -1.42, 0.0], "angular_velocity": [-0.10, 0.00, -0.52],
      "bounding_box_vertices": [[35.28, -69.01, -0.00], "... 8 vertices"], "extra": {}
    },
    "other_actors": {
      "143": {"type_id": "vehicle.lincoln.mkz_2017", "location": [34.74, -65.76, 0.03],
              "rotation": [0.02, 0.00, -82.52], "velocity": [0.57, -4.97, 0.0],
              "angular_velocity": [0.13, -0.04, -6.19],
              "bounding_box_vertices": [[33.37, -63.47, -0.00], "... 8 vertices"], "extra": {}}
    }
  }, "..."],

  "sotif": {
    "odd_values": {"weather": "WetNoon", "time_of_day": "dusk", "road_condition": "wet",
                   "traffic_density": "low", "pedestrian_density": "low",
                   "operational_speed": "low", "mission_duration": "short"},
    "triggering_conditions": ["critical_ttc", "low_friction_surface", "low_visibility_time", "near_miss_distance"]
  }
}
```

## Fields

| Field | Written by | Meaning |
|---|---|---|
| `tool` | Recorder | Name of the output folder (Runner) or of the integrating tool |
| `generation_id` | Recorder | Driver of the ego: `behavior_agent` or `autoware` (Runner) |
| `scenario_id`, `run_index` | Recorder | Scenario file name and run number; they match the file name |
| `delta_time` | Recorder | Simulation step; read from the CARLA world when it is synchronous with a fixed step |
| `world_state` | Recorder | Raw facts at the start of the run, input of the ODD and TC evaluation ([enrichment.md](enrichment.md)). `ego_speed_limit_kmh` is in km/h |
| `results.event_counts` | Recorder | Occurrences of each event during the run. The hazards of the configuration are read from here; a missing key counts as 0. `collision` is the total of the three collision categories |
| `results.has_*` | Recorder | True once the event occurred |
| `results.total_frames`, `simulated_time` | Recorder | Number of logged frames; simulated duration |
| `results.total_wall_clock_time` | Recorder | Real duration, from the first logged step to the end of the run |
| `events.<name>` | Recorder | Details of each event: frame, timestamp, and context (the collision entry records the actors, impulse and impact side). `event_counts` is authoritative for counting; the `red_light`, `stop_sign` and `speeding` lists may hold two entries for one occurrence, with slightly different field names (known issue, not fixed yet) |
| `mission` | Integrations only | Planned route of the ego, set with `set_mission_from_agent()`. The Runner leaves it empty |
| `actors` | Recorder | Dynamic actors (vehicles, pedestrians) with role (`ego`/`npc`) and spawn pose `[x, y, z, roll, pitch, yaw]`. An NPC is registered the first frame it is seen |
| `frames[]` | Recorder | One entry per step: `timestamp` is relative to the first step; `ego_vehicle` and each `other_actors` entry hold pose, velocity and the 8 world-space vertices of the bounding box |
| `results.critical_metrics`, `dynamics_metrics`, `functional_metrics` | Evaluation, step 0.5 | See [enrichment.md](enrichment.md) |
| `results.events` | Evaluation, step 0.5 | Normalized copy of `events` (event names, frame and timestamp conventions) |
| `sotif.odd_values`, `sotif.triggering_conditions` | Evaluation, step A | Value of each ODD factor and list of fired TCs in this run |

Pose arrays: `location` is `[x, y, z]`, `rotation` is `[roll, pitch, yaw]`, `velocity` and `angular_velocity` are `[x, y, z]`.
