"""
recorder.py

Bridges a running Scenic/CARLA simulation to this repo's existing logging
stack (data_gathering.carlaBasicLogger.CarlaBasicLogger + ViolationMonitor)
without requiring any change to that stack.

Scenic's simulator.simulate() owns the tick loop internally, so there is no
external per-tick hook. Instead, scenic_carla_runner.py injects a small Scenic
`monitor` into a temporary copy of each .scenic file; that monitor calls
on_monitor_step() once per simulated step, passing the shared RunnerContext
and the scenario's `ego` object (Scenic's driving domain always binds `ego`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import carla

from data_gathering.carlaBasicLogger import CarlaBasicLogger, LOGGER_REGISTRY
from data_gathering.violationMonitor import ViolationMonitor


class GoalReached(RuntimeError):
    """Raised from on_monitor_step() when Autoware reports it has arrived.

    An extra end condition for --engine autoware only; it stops the run early
    but is otherwise treated exactly like a normal completion, so it changes
    nothing in the metrics.
    """


class RunWallClockTimeout(RuntimeError):
    """Raised from on_monitor_step() when a run's real (wall-clock) time
    exceeds RunnerContext.wall_timeout_s.

    maxSteps (see ScenicCarlaRunner) only bounds *simulated* time - it
    assumes each tick costs roughly one real-time timestep. During a pileup
    CARLA's physics solver can take many real seconds per tick, so the step
    count stays capped while wall-clock time keeps growing unbounded. This
    exception gives the runner a real-time backstop for that case.
    """


@dataclass
class RunnerContext:
    world: Optional[carla.World]
    client: Optional[carla.Client]
    tool: str
    generation_id: str
    scenario_id: str
    run_index: int
    output_dir: str
    delta_time: float
    timeout_s: float
    wall_timeout_s: Optional[float] = None
    run_started_at: Optional[float] = None

    logger: Optional[CarlaBasicLogger] = None
    violation_monitor: Optional[ViolationMonitor] = None
    collision_sensor: Optional[carla.Actor] = None
    lane_sensor: Optional[carla.Actor] = None
    ego_actor_id: Optional[int] = None
    world_state: Dict[str, Any] = field(default_factory=dict)

    # --engine autoware only; None for behavior_agent, which leaves every
    # Autoware-specific branch below dead.
    autoware_session: Optional[Any] = None
    _steps: int = 0

    def destroy_sensors(self) -> None:
        for sensor in (self.collision_sensor, self.lane_sensor):
            if sensor is not None and sensor.is_alive:
                try:
                    sensor.stop()
                    sensor.destroy()
                except RuntimeError:
                    pass
        if self.ego_actor_id is not None and self.ego_actor_id in LOGGER_REGISTRY:
            del LOGGER_REGISTRY[self.ego_actor_id]


def snapshot_world_state(ctx: RunnerContext, ego_actor: carla.Vehicle) -> Dict[str, Any]:
    """Raw, generic facts about the scenario's starting state - no
    interpretation/bucketing here (that happens once, generically, in
    data_gathering/enriching/compute_sotif_odd.py's compute_derived_fields())."""
    world = ctx.world
    weather = world.get_weather()
    actors = world.get_actors()
    vehicles = actors.filter("vehicle.*")
    walkers = actors.filter("walker.pedestrian.*")

    try:
        speed_limit_kmh = float(ego_actor.get_speed_limit())
    except Exception:
        speed_limit_kmh = None

    return {
        "map_name": world.get_map().name.split("/")[-1],
        "cloudiness": weather.cloudiness,
        "precipitation": weather.precipitation,
        "precipitation_deposits": weather.precipitation_deposits,
        "wetness": weather.wetness,
        "wind_intensity": weather.wind_intensity,
        "fog_density": weather.fog_density,
        "sun_altitude_angle": weather.sun_altitude_angle,
        "num_npc_vehicles": max(0, len(vehicles) - 1),  # exclude ego itself
        "num_pedestrians": len(walkers),
        "ego_speed_limit_kmh": speed_limit_kmh,
        "mission_timeout_s": ctx.timeout_s,
        "num_waypoints": 0,  # not generically available across arbitrary .scenic behaviors
    }


def _first_time_setup(ctx: RunnerContext, ego_actor: carla.Vehicle) -> None:
    logger = CarlaBasicLogger(
        tool=ctx.tool,
        generation_id=ctx.generation_id,
        scenario_id=ctx.scenario_id,
        output_dir=ctx.output_dir,
        world=ctx.world,
        client=ctx.client,
        delta_time=ctx.delta_time,
        record_binary=False,
        run_index=ctx.run_index,
    )

    snapshot = ctx.world.get_snapshot()
    logger.register_ego_actor(ego_vehicle=ego_actor, snapshot=snapshot)

    violation_monitor = ViolationMonitor(ego_vehicle=ego_actor, logger=logger)
    logger.violation_monitor = violation_monitor

    LOGGER_REGISTRY[ego_actor.id] = logger

    bp_lib = ctx.world.get_blueprint_library()

    collision_bp = bp_lib.find("sensor.other.collision")
    collision_sensor = ctx.world.spawn_actor(
        collision_bp, carla.Transform(), attach_to=ego_actor
    )
    collision_sensor.listen(lambda event: CarlaBasicLogger.handle_collision(event, {}))

    lane_bp = bp_lib.find("sensor.other.lane_invasion")
    lane_sensor = ctx.world.spawn_actor(lane_bp, carla.Transform(), attach_to=ego_actor)

    def _on_lane_invasion(event: carla.LaneInvasionEvent) -> None:
        frame = logger.frame_count
        timestamp = ctx.world.get_snapshot().timestamp.elapsed_seconds
        crossed = [str(marking.type) for marking in event.crossed_lane_markings]
        logger.log_lane_invasion(frame=frame, timestamp=timestamp, crossed_markings=crossed)

    lane_sensor.listen(_on_lane_invasion)

    ctx.logger = logger
    ctx.violation_monitor = violation_monitor
    ctx.collision_sensor = collision_sensor
    ctx.lane_sensor = lane_sensor
    ctx.ego_actor_id = ego_actor.id

    ctx.world_state = snapshot_world_state(ctx, ego_actor)
    logger.scenario_data["world_state"] = ctx.world_state


def on_monitor_step(ctx: RunnerContext, ego: Any) -> None:
    """Called once per simulated step by the injected Scenic monitor."""
    if ctx.wall_timeout_s is not None and ctx.run_started_at is not None:
        elapsed = time.time() - ctx.run_started_at
        if elapsed > ctx.wall_timeout_s:
            raise RunWallClockTimeout(
                f"[{ctx.scenario_id}] run {ctx.run_index}: exceeded wall-clock cap of "
                f"{ctx.wall_timeout_s:.0f}s (elapsed {elapsed:.0f}s) - simulation ticks are "
                "taking far longer than real time (e.g. a stalled pileup), so the step-count "
                "cap alone would never have stopped it."
            )

    ego_actor = ego.carlaActor
    if ctx.logger is None:
        _first_time_setup(ctx, ego_actor)

    ctx._steps += 1

    if ctx.autoware_session is not None:
        # Engage on the first step, not before the loop: Autoware only offers
        # autonomous mode once planning has produced a trajectory, and that
        # needs the world to be ticking - which only starts here. The call
        # runs on its own thread so the tick loop is not blocked.
        if ctx._steps == 1 and not getattr(ctx.autoware_session, "engaged", False):
            # Fallback only: prepare() normally engages up front, while the
            # tick pump is running. This covers the case where it did not.
            ctx.autoware_session.engage_background()
        # Arrival is polled sparsely: each check is a ~1 s round trip into WSL,
        # so once every 400 steps (20 simulated seconds at 0.05 s) rather than
        # per tick.
        elif ctx._steps % 400 == 0 and ctx.autoware_session.goal_reached():
            raise GoalReached(
                f"[{ctx.scenario_id}] run {ctx.run_index}: Autoware reached its goal"
            )

    snapshot = ctx.world.get_snapshot()
    ctx.logger.update_frame(world=ctx.world, ego_vehicle=ego_actor, snapshot=snapshot)
