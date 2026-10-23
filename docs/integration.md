# Integration guide

How to make another CARLA-based tool produce execution logs that the SAFE-SCoRE evaluation can process. If your scenarios are Scenic scenarios, use the Runner instead (see the [README](../README.md)): it already does everything below.

The integration uses two modules of `src/data_gathering/`:

- `carlaBasicLogger.py` (`CarlaBasicLogger`, `LOGGER_REGISTRY`): builds and saves the log of one execution.
- `violationMonitor.py` (`ViolationMonitor`): detects red-light, stop-sign, speeding and off-road events at every frame and reports them to the logger.

`src/runner/recorder.py` is a working example of the same wiring.

## Requirements on your tool

- The CARLA world runs in **synchronous mode with a fixed time step** (`synchronous_mode = True`, `fixed_delta_seconds > 0`). `ViolationMonitor` derives its frame rate from it.
- The logger is updated **once per simulation tick**, from the thread that ticks.
- One logger per execution; the ego vehicle is a `carla.Vehicle`.

## Steps

### 1. Make the modules importable

`carlaBasicLogger.py` imports `utils.carla_help`, so both `src/` and the repository root must be on `sys.path`:

```python
import sys
sys.path.insert(0, "/path/to/SAFE-SCoRE/src")

from data_gathering.carlaBasicLogger import CarlaBasicLogger, LOGGER_REGISTRY
from data_gathering.violationMonitor import ViolationMonitor
```

### 2. Create the logger, once per execution

```python
logger = CarlaBasicLogger(
    tool="YourTool",
    generation_id="gen_0001",
    scenario_id=scenario_id,
    output_dir="outputs/YourTool",
    world=world,
    client=client,
    record_binary=False,      # True also stores CARLA's binary recording next to the log
    run_index=run_index,
)
```

`output_dir` is the dataset folder that the evaluation will process.

### 3. Register the ego, attach the violation monitor

Right after the ego is spawned:

```python
logger.register_ego_actor(ego_vehicle=ego_vehicle, snapshot=world.get_snapshot())

LOGGER_REGISTRY[ego_vehicle.id] = logger      # collision callbacks find the logger here
logger.violation_monitor = ViolationMonitor(ego_vehicle=ego_vehicle, logger=logger)
```

### 4. Forward the sensor events

```python
collision_sensor.listen(lambda event: CarlaBasicLogger.handle_collision(event, {}))
```

`collision_sensor` is a `sensor.other.collision` attached to the ego. The second argument is a tool-specific state object; pass `{}` if there is none. Lane invasions and off-road are optional: for lane invasion, attach a `sensor.other.lane_invasion` and call `logger.log_lane_invasion(frame, timestamp, crossed_markings)` from its callback (see `_first_time_setup()` in `src/runner/recorder.py`); off-road is detected by the monitor.

### 5. Record the world state

The ODD and TC evaluation reads the `world_state` block. Set it once, at the start of the execution:

```python
weather = world.get_weather()
logger.scenario_data["world_state"] = {
    "map_name": world.get_map().name.split("/")[-1],
    "cloudiness": weather.cloudiness,
    "precipitation": weather.precipitation,
    "precipitation_deposits": weather.precipitation_deposits,
    "wetness": weather.wetness,
    "wind_intensity": weather.wind_intensity,
    "fog_density": weather.fog_density,
    "sun_altitude_angle": weather.sun_altitude_angle,
    "num_npc_vehicles": n_vehicles_without_ego,
    "num_pedestrians": n_pedestrians,
    "ego_speed_limit_kmh": ego_vehicle.get_speed_limit(),
    "mission_timeout_s": scenario_duration_s,
}
```

`snapshot_world_state()` in `src/runner/recorder.py` builds the same dictionary. A missing field falls back to a default or to `unknown`, which distorts the ODD values: provide all of them. Your own ODD factors may use other fields ([enrichment.md](enrichment.md)).

### 6. Optional: the mission

If your ego controller exposes a route, `logger.set_mission_from_agent(ego_agent, ego_sp, ego_dp)` stores it (written for a CARLA `BehaviorAgent`-style interface; adapt it for other controllers). It only feeds the functional metrics, which no report uses.

### 7. Update the logger at every tick

```python
while running:
    world.tick()
    logger.update_frame(world=world, ego_vehicle=ego_vehicle, snapshot=world.get_snapshot())
```

This logs the frame and runs the violation monitor.

### 8. Finalize on every exit path

```python
logger.finalize_and_save(filename=f"{scenario_id}_run_{run_index:02d}_log_basic.json")
```

Call it at normal termination, on early stops and in exception cleanup. At least one frame must have been logged. **The file name matters:** the evaluation steps that compute hazards, execution times and coverage select files named `<scenario_id>_run_<NN>_log_basic.json`; with the default name (`<generation_id>_<scenario_id>_log_basic.json`) only the metrics and ODD steps would see the log. Several runs of the same scenario share `scenario_id` and differ in `NN`.

## Minimal example

```python
import sys
import carla
sys.path.insert(0, "/path/to/SAFE-SCoRE/src")
from data_gathering.carlaBasicLogger import CarlaBasicLogger, LOGGER_REGISTRY
from data_gathering.violationMonitor import ViolationMonitor

client = carla.Client("localhost", 2000)
client.set_timeout(10.0)
world = client.get_world()
settings = world.get_settings()
settings.synchronous_mode = True
settings.fixed_delta_seconds = 0.05
world.apply_settings(settings)

bp_lib = world.get_blueprint_library()
ego = world.spawn_actor(bp_lib.find("vehicle.tesla.model3"), world.get_map().get_spawn_points()[0])

scenario_id, run_index = "scenario_0001", 1
logger = CarlaBasicLogger(tool="ExampleTool", generation_id="gen_0001", scenario_id=scenario_id,
                          output_dir="outputs/ExampleTool", world=world, client=client, run_index=run_index)
logger.register_ego_actor(ego_vehicle=ego, snapshot=world.get_snapshot())
LOGGER_REGISTRY[ego.id] = logger
logger.violation_monitor = ViolationMonitor(ego_vehicle=ego, logger=logger)

weather = world.get_weather()
logger.scenario_data["world_state"] = {
    "map_name": world.get_map().name.split("/")[-1],
    "cloudiness": weather.cloudiness, "precipitation": weather.precipitation,
    "precipitation_deposits": weather.precipitation_deposits, "wetness": weather.wetness,
    "wind_intensity": weather.wind_intensity, "fog_density": weather.fog_density,
    "sun_altitude_angle": weather.sun_altitude_angle,
    "num_npc_vehicles": 0, "num_pedestrians": 0,
    "ego_speed_limit_kmh": ego.get_speed_limit(), "mission_timeout_s": 25.0,
}

collision_sensor = world.spawn_actor(bp_lib.find("sensor.other.collision"), carla.Transform(), attach_to=ego)
collision_sensor.listen(lambda event: CarlaBasicLogger.handle_collision(event, {}))

try:
    for _ in range(500):
        # your scenario logic here (control the ego, move the NPCs)
        world.tick()
        logger.update_frame(world=world, ego_vehicle=ego, snapshot=world.get_snapshot())
finally:
    logger.finalize_and_save(filename=f"{scenario_id}_run_{run_index:02d}_log_basic.json")
    collision_sensor.destroy()
    ego.destroy()
```

## Evaluate

With the logs of all your executions in `outputs/<name>/` (run from the repository root, with the virtual environment active):

```bash
python -m src.pipeline.run_pipeline --output_folder <name>
```

The reports are written next to the logs and are described in the README (Output). The log schema is in [base_log_json.md](base_log_json.md).
