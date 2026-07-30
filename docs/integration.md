# Integration Guide

## Overview

This document explains how to integrate the logging modules into a CARLA-based scenario generator so that each executed scenario produces a JSON log compatible with the project pipelines.

The integration is based on two core modules:

- `carlaBasicLogger.py`
- `violationMonitor.py`

Once integrated, each scenario execution produces a **base log** (`*_log_basic.json`) that can later be processed by the SOTIF and analysis pipelines.

---

## Goal of the integration

The purpose of this integration is to ensure that every generated scenario is executed in CARLA with a uniform data collection mechanism.  
The resulting log contains:

- scenario metadata;
- mission information for the ego vehicle;
- frame-by-frame state of the ego and surrounding dynamic actors;
- safety-relevant events such as:
  - collisions,
  - red-light violations,
  - speeding,
  - stop-sign violations,
  - off-road events.

These logs are the input expected by the downstream processing pipeline.

---

## Required modules

The two modules live at `src/data_gathering/carlaBasicLogger.py` and `src/data_gathering/violationMonitor.py` in this repository. Copy them into the scenario generator source tree, or make them importable from your runtime environment.

At minimum, the following modules must be available:

```python
from carlaBasicLogger import CarlaBasicLogger, LOGGER_REGISTRY
from violationMonitor import ViolationMonitor
```

If your project uses a package structure, adapt the import paths accordingly.

## Integration steps
### 1. Import the logger modules
In the script responsible for scenario creation and execution, import the logger and the violation monitor:

```python
from carlaBasicLogger import CarlaBasicLogger, LOGGER_REGISTRY
from violationMonitor import ViolationMonitor
```

### 2. Instantiate the logger
Create one CarlaBasicLogger instance for each scenario execution.


```python
logger = CarlaBasicLogger(
    tool="YourToolName",
    generation_id=generation_id,
    scenario_id=scenario_id,
    output_dir=output_dir,
    world=world,
    client=client,
    record_binary=True,
    run_index=run_index
)
```

### 3. Register the ego vehicle
Immediately after the ego vehicle is spawned, register it in the logger.

```python
snapshot = world.get_snapshot()
logger.register_ego_actor(ego_vehicle=ego_vehicle, snapshot=snapshot)
```

This stores the static identity of the ego actor in the log.

### 4. Register the ego mission
If your ego controller exposes route and destination information, store the mission as soon as it becomes available.

```python
logger.set_mission_from_agent(
    ego_agent=ego_agent,
    ego_sp=ego_start_point,
    ego_dp=ego_destination_point
)
```

*Important note*

The current implementation is designed around a BehaviorAgent-style interface.
If your generator uses a different controller abstraction, you may need to adapt the mission extraction logic.

### 5. Create and attach the violation monitor
Instantiate the ViolationMonitor and attach it to the logger.

```python
LOGGER_REGISTRY[ego_vehicle.id] = logger

violation_monitor = ViolationMonitor(
    ego_vehicle=ego_vehicle,
    logger=logger
)

logger.violation_monitor = violation_monitor
```

This step is required because:
 - the logger uses the monitor during frame updates;
 - collision callbacks resolve the correct logger through LOGGER_REGISTRY.


### 6. Attach collision handling

Collision events must be forwarded to the logger through the static callback.

```python
collision_sensor.listen(
    lambda event: CarlaBasicLogger.handle_collision(event, state)
)
```

*Notes*

- state is the scenario/tool state object already used by the generator, if any.
- The collision callback internally resolves the logger associated with the ego vehicle.
- If your tool does not require a custom state object, you can pass an empty dictionary:

```python
collision_sensor.listen(
    lambda event: CarlaBasicLogger.handle_collision(event, {})
)
```

### 7. Update the logger at every simulation tick

Inside the main simulation loop, update the logger once per frame.

```python
while simulation_running:
    world.tick()
    snapshot = world.get_snapshot()

    logger.update_frame(
        world=world,
        ego_vehicle=ego_vehicle,
        snapshot=snapshot
    )

    # scenario logic here
```

This step is mandatory because it performs both:
 - frame-by-frame logging of the scene;
 - violation monitoring through the attached ViolationMonitor.

### 8. Finalize and save the log
At the end of the scenario execution, always finalize the logger and save the output JSON.

```python
logger.finalize_and_save()
```

This should be done:
 - at normal scenario termination;
 - in early-stop conditions;
 - in exception-safe cleanup blocks whenever possible.

## Minimal integration example

```python
import carla

from carlaBasicLogger import CarlaBasicLogger, LOGGER_REGISTRY
from violationMonitor import ViolationMonitor

# --------------------------------------------------
# Connect to CARLA
# --------------------------------------------------
client = carla.Client("localhost", 2000)
client.set_timeout(10.0)
world = client.get_world()

# --------------------------------------------------
# Spawn ego vehicle
# --------------------------------------------------
blueprint_library = world.get_blueprint_library()
vehicle_bp = blueprint_library.find("vehicle.tesla.model3")
spawn_point = world.get_map().get_spawn_points()[0]

ego_vehicle = world.spawn_actor(vehicle_bp, spawn_point)

# --------------------------------------------------
# Example metadata
# --------------------------------------------------
tool_name = "ExampleGenerator"
generation_id = "gen_0001"
scenario_id = "scenario_0001"
run_index = 1
output_dir = "outputs/ExampleGenerator"

# --------------------------------------------------
# Instantiate logger
# --------------------------------------------------
logger = CarlaBasicLogger(
    tool=tool_name,
    generation_id=generation_id,
    scenario_id=scenario_id,
    output_dir=output_dir,
    world=world,
    client=client,
    record_binary=True,
    run_index=run_index
)

# --------------------------------------------------
# Register ego
# --------------------------------------------------
snapshot = world.get_snapshot()
logger.register_ego_actor(ego_vehicle=ego_vehicle, snapshot=snapshot)

# --------------------------------------------------
# Optional: register mission if an agent is available
# --------------------------------------------------
# logger.set_mission_from_agent(
#     ego_agent=ego_agent,
#     ego_sp=ego_start_point,
#     ego_dp=ego_destination_point
# )

# --------------------------------------------------
# Attach violation monitor
# --------------------------------------------------
LOGGER_REGISTRY[ego_vehicle.id] = logger

violation_monitor = ViolationMonitor(
    ego_vehicle=ego_vehicle,
    logger=logger
)
logger.violation_monitor = violation_monitor

# --------------------------------------------------
# Collision sensor
# --------------------------------------------------
collision_bp = blueprint_library.find("sensor.other.collision")
collision_sensor = world.spawn_actor(
    collision_bp,
    carla.Transform(),
    attach_to=ego_vehicle
)

collision_sensor.listen(
    lambda event: CarlaBasicLogger.handle_collision(event, {})
)

# --------------------------------------------------
# Main simulation loop
# --------------------------------------------------
try:
    for _ in range(500):
        world.tick()
        snapshot = world.get_snapshot()

        logger.update_frame(
            world=world,
            ego_vehicle=ego_vehicle,
            snapshot=snapshot
        )

        # your scenario execution logic here

finally:
    logger.finalize_and_save()
    collision_sensor.destroy()
    ego_vehicle.destroy()
```

### Expected output
The logger saves one JSON file per scenario execution.

For most tools, the default filename pattern is:

```text
<generation_id>_<scenario_id>_log_basic.json
```
A tool may override this convention if needed, but the produced file must remain a base log compatible with the downstream pipeline.


### Output directory recommendation
To simplify downstream processing, it is recommended to save logs directly inside the dataset folder associated with the generator, for example:

```text
outputs/
└── YourToolName/
    ├── gen_0001_sc_0001_log_basic.json
    ├── gen_0001_sc_0002_log_basic.json
    └── ...
```

This is consistent with the pipeline expectation that dataset directories contain base logs ready for enrichment and analysis.

## Next step
Once the base logs have been generated, they can be processed through the project pipelines (run from the repository root):
 1. Run the SOTIF pipeline: `python -m src.pipeline.run_pipeline`
 2. Run the analysis pipeline: `python -m src.analysis.run_analysis`

If your generator is Scenic-based, consider using `src/runner/` (see the main `README.md`) instead of this manual integration - it already wires up `CarlaBasicLogger`/`ViolationMonitor` for you.
