"""
autoware_session.py

The ownership split that lets a Scenic scenario run with Autoware driving the ego.

Scenic and Autoware both assume they own the simulation. Rather than making
either pretend otherwise, responsibility is divided:

    Autoware  owns the map, the ego vehicle, its sensors and its control
    Scenic    owns the clock and every other actor, including their behaviours
    SAFE-SCoRE decides where the ego starts and where it is going, and logs

Scenic keeps the clock because its simulate() loop is also what executes NPC
behaviours - the cut-ins and sudden brakes that actually generate hazards.
Autoware is stopped from ticking by the CARLA_EXTERNAL_TICK environment
variable (see the one-line patch in carla_autoware.py).

Everything here is applied as runtime monkeypatches over Scenic, in the same
style already used in scenic_carla_runner (_tuned_opendrive_generation,
_spawn_diagnostics), so no fork of Scenic is required. All patches are undone
on exit, and none of this is reachable from --engine behavior_agent.
"""

from __future__ import annotations

import contextlib
from dataclasses import replace
import logging
import random
import threading
import time
from typing import Any, Dict, Optional

import carla

from runner.autoware_control import AutowareController, RoutingServiceDown
from runner.goal_planner import (
    REALISM_FACTOR,
    EgoDynamics,
    GoalResult,
    compute_goal,
)

log = logging.getLogger(__name__)

EGO_ROLE_NAME = "ego_vehicle"


class AutowareSessionError(RuntimeError):
    """Raised when the session cannot be established for a run."""


def find_ego_actor(world: carla.World, timeout_s: float = 20.0) -> carla.Vehicle:
    """Locate the ego Autoware spawned, by role_name."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for actor in world.get_actors().filter("vehicle.*"):
            if actor.attributes.get("role_name") == EGO_ROLE_NAME:
                return actor
        time.sleep(1.0)
    raise AutowareSessionError(
        f"no vehicle with role_name='{EGO_ROLE_NAME}' in the world - is Autoware running?"
    )


class AutowareSession:
    """Per-run orchestration of the Autoware side."""

    def __init__(
        self,
        *,
        controller: Optional[AutowareController] = None,
        dyn: Optional[EgoDynamics] = None,
        time_limit_s: float = 60.0,
        step_period_s: float = 0.05,
        rng: Optional[random.Random] = None,
    ) -> None:
        # Wall-clock seconds each simulated step must take. Autoware is a
        # real-time stack: its perception/planning/control take real time no
        # matter what the simulation clock says, so a world advancing faster
        # than real time leaves its commands arriving far too late and the ego
        # barely moves. Autoware's own bridge paces itself the same way
        # (max_real_delta_seconds); Scenic has no such throttle.
        self.step_period_s = step_period_s
        # The world is attached later: the ownership patches must already be
        # in place when CarlaSimulator is constructed (so it does not reload
        # the map), and only then is sim.world available.
        self.world: Optional[carla.World] = None
        self.carla_map: Optional[carla.Map] = None
        self.aw = controller or AutowareController()
        self.dyn = dyn or self.aw.get_dynamics()
        self.time_limit_s = time_limit_s
        self.rng = rng or random.Random()

        self.ego_actor: Optional[carla.Vehicle] = None
        self.goal: Optional[GoalResult] = None
        self._engage_thread: Optional[threading.Thread] = None
        self.engaged = False

    def attach(self, world: carla.World) -> None:
        self.world = world
        self.carla_map = world.get_map()

    # --- setup, before the simulation starts ------------------------------
    def prepare(
        self, ego_start: carla.Transform, target_speed: Optional[float] = None
    ) -> GoalResult:
        """Place the ego at the sampled scene's ego pose and set its goal.

        Runs before Scenic starts ticking, so that by the time the simulation
        begins the car is in the right place and Autoware already has a route.
        """
        self.ego_actor = find_ego_actor(self.world)

        # Match Autoware's planning speed to what this scene expects, and
        # budget the goal distance against the same number - otherwise the
        # goal is computed for one speed and driven at another.
        if target_speed:
            if self.aw.set_velocity_limit(target_speed):
                self.dyn = replace(
                    self.dyn, v_lon_max=target_speed, v_max=target_speed
                )

        start_pose = _carla_to_autoware(ego_start)
        log.info(
            "[autoware] placing ego at carla=(%.1f, %.1f) yaw=%.1f",
            ego_start.location.x,
            ego_start.location.y,
            ego_start.rotation.yaw,
        )
        if not self.aw.publish_initial_pose(start_pose):
            raise AutowareSessionError("could not teleport the ego")

        # The bridge drops the car from 2 m up (initialpose_callback does
        # position.z += 2.0), so straight after the teleport it is in free
        # fall - and localization refuses to initialize on a moving vehicle
        # ("The vehicle is not stopped."). Wait for it to actually settle
        # rather than guessing a delay; the tick pump is running, so it does.
        if not self._wait_until_stopped():
            log.warning("[autoware] ego still moving; initializing localization anyway")
        if not self.aw.initialize_localization(start_pose):
            raise AutowareSessionError(
                "localization would not initialize at the scene's ego pose - "
                "without it Autoware never becomes engageable"
            )

        self.goal = self._set_goal_with_retries(ego_start)

        # Engage here, while the tick pump is still running, rather than from
        # inside the tick loop. Autoware only becomes engageable once planning
        # has produced a trajectory, which costs both simulated time and a
        # ~1 s round trip per poll - and a 20 s scenario compresses into a few
        # wall seconds, so a run can easily finish before engage lands. Doing
        # it up front means the car is already in autonomous mode when the
        # first frame is logged; it has not moved yet, because ticking stops
        # the moment this returns.
        self.engaged = self.aw.engage(wait_s=90.0)
        if not self.engaged:
            # Distinguish "not ready yet" from "the planner is dead". A
            # segfaulted motion_planning_container publishes no trajectory,
            # so every later run silently produces a stationary ego and a 0 m
            # log - which looks like a logic bug and is not one. Naming it here
            # turns an hour of tick-rate debugging into one line.
            if not self.aw.planning_alive():
                raise AutowareSessionError(
                    "Autoware is not producing a trajectory at all - its planning "
                    "stack is very likely dead (check the launch terminal for "
                    "'process has died', e.g. motion_planning_container). "
                    "Autoware must be restarted; no amount of retrying will help."
                )
            raise AutowareSessionError(
                "Autoware would not engage - it never offered autonomous mode. "
                "Check the launch terminal's diagnostics for the blocking component."
            )
        log.info("[autoware] engaged, ready to drive")
        return self.goal

    # Distance multipliers tried in order when a goal is refused. The first two
    # are full distance: because the walk is random, simply resampling often
    # lands somewhere Autoware likes. Later attempts shorten the route, which
    # keeps the goal away from the far junctions that tend to be refused.
    _GOAL_BACKOFF = (1.0, 1.0, 0.8, 0.6, 0.45)

    def _set_goal_with_retries(self, ego_start: carla.Transform) -> GoalResult:
        """Compute and set a goal, resampling if Autoware refuses it.

        Two distinct rejections hide behind the same failure:

        - "The route is already set." Autoware will not accept a new route
          while one exists, and the previous run's route can survive if its
          cleanup did not land. Nothing to do with the goal, so every attempt
          clears first rather than resampling a geometry that was never the
          problem.
        - "Goal's footprint exceeds lane!" A genuine geometric refusal. The
          walk follows CARLA's lane graph while Autoware validates against its
          lanelet2 map, and the two disagree in places - typically near
          junctions. Neither map is wrong, they are different models of the
          same road, so resampling elsewhere beats trying to predict the
          disagreement.
        """
        last = ""
        for attempt, factor in enumerate(self._GOAL_BACKOFF, 1):
            goal = compute_goal(
                self.carla_map,
                ego_start,
                self.time_limit_s,
                dyn=self.dyn,
                realism_factor=REALISM_FACTOR * factor,
                rng=self.rng,  # advances, so each attempt is a different route
            )
            # Clear only when a route is actually set. Clearing
            # unconditionally on every attempt hammers the mission planner -
            # and its container was seen to segfault under that churn, after
            # which set_route_points has no server and simply times out.
            if self.aw.routing_state() == 2:
                self.aw.clear_route()
            if self.aw.set_goal(goal.goal_autoware):
                log.info("[autoware] %s (attempt %d)", goal.summary(), attempt)
                return goal
            last = goal.summary()
            log.info(
                "[autoware] goal refused (attempt %d/%d, %.0f%% distance); resampling",
                attempt, len(self._GOAL_BACKOFF), factor * 100,
            )
        raise AutowareSessionError(
            f"Autoware refused every candidate goal ({len(self._GOAL_BACKOFF)} tried, "
            f"last: {last}). The ego may be somewhere its lanelet map cannot route from."
        )

    def _wait_until_stopped(
        self, speed_eps: float = 0.1, timeout_s: float = 15.0, stable_for_s: float = 0.7
    ) -> bool:
        """Wait until the ego has come to rest after being teleported.

        Two checks, because the two sides disagree for a moment. The CARLA
        actor's velocity is instantaneous and settles first; Autoware's own
        estimate arrives through the bridge's sensor loop and lags behind. The
        initialize service checks Autoware's value, so waiting only on CARLA's
        is what produced "The vehicle is not stopped." on an already-stationary
        car.
        """
        assert self.ego_actor is not None
        deadline = time.time() + timeout_s
        still_since: Optional[float] = None
        while time.time() < deadline:
            v = self.ego_actor.get_velocity()
            speed = (v.x**2 + v.y**2 + v.z**2) ** 0.5
            now = time.time()
            if speed < speed_eps:
                still_since = still_since or now
                if now - still_since >= stable_for_s:
                    break
            else:
                still_since = None
            time.sleep(0.1)
        else:
            return False

        # Now let Autoware's own view catch up.
        for _ in range(4):
            aw_speed = self.aw.vehicle_speed()
            if aw_speed is not None and aw_speed < speed_eps:
                log.info(
                    "[autoware] ego settled (carla %.2f, autoware %.2f m/s)", speed, aw_speed
                )
                return True
            time.sleep(1.0)
        log.warning("[autoware] Autoware still reports the ego moving after settling")
        return False

    # --- during the simulation --------------------------------------------
    def engage_background(self) -> None:
        """Engage autonomous mode without blocking the tick loop.

        Autoware only offers autonomous mode once planning has produced a
        trajectory, which needs the world to be ticking - and the ticking is
        Scenic's simulate() loop, which we are inside. So engage runs on its
        own thread and the simulation carries on meanwhile; the ego simply
        stays still for the second or two until it takes effect.
        """
        if self._engage_thread is not None:
            return

        def _worker() -> None:
            self.engaged = self.aw.engage(wait_s=90.0)
            log.info("[autoware] engage -> %s", self.engaged)

        self._engage_thread = threading.Thread(
            target=_worker, name="autoware-engage", daemon=True
        )
        self._engage_thread.start()

    def goal_reached(self) -> bool:
        """True once Autoware reports ARRIVED (routing state 3)."""
        return self.aw.routing_state() == 3

    # --- teardown, between runs -------------------------------------------
    def finish(self) -> None:
        """Return Autoware to a clean state, ready for the next run.

        Deliberately does not relaunch anything: Autoware boots once per suite
        and each subsequent run is a few service calls.
        """
        if self._engage_thread is not None and self._engage_thread.is_alive():
            self._engage_thread.join(timeout=5.0)
        try:
            self.aw.reset_for_next_run()
        except Exception:  # never let cleanup break the run's logging
            log.warning("[autoware] reset failed", exc_info=True)


class TickPump:
    """Ticks the world in the background while nothing else is.

    Taking the tick away from Autoware creates a gap: in synchronous mode the
    world only advances when *someone* ticks, and Scenic only starts ticking
    inside simulate(). Between those two points Autoware is stuck - it cannot
    finish spawning its ego or its sensors, cannot converge localization, and
    cannot produce the trajectory that makes it engageable. Symptom: the bridge
    starts, prints nothing at all, and no ego ever appears.

    So SAFE-SCoRE pumps ticks from the moment it takes ownership until Scenic's
    loop takes over. The pump must be stopped before simulate() begins - two
    tick masters is exactly what this whole design exists to avoid.
    """

    def __init__(
        self,
        world: carla.World,
        rate_hz: float = 20.0,
        client: Optional[carla.Client] = None,
    ) -> None:
        self.world = world
        # Optional, but required when pumping across an Autoware *startup*:
        # the bridge calls client.load_world(), which invalidates every
        # existing world handle. Without a client to re-acquire from, the pump
        # silently dies at exactly the moment Autoware needs it most.
        self.client = client
        self.period = 1.0 / rate_hz
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return

        def _worker() -> None:
            while not self._stop.is_set():
                started = time.time()
                try:
                    self.world.tick()
                except RuntimeError:
                    if self.client is None:
                        break  # world went away and we cannot get another
                    try:
                        self.world = self.client.get_world()
                        continue
                    except RuntimeError:
                        time.sleep(1.0)
                        continue
                # Sleep only the remainder of the period, not a full period on
                # top of the tick. A tick with the sensor kit attached costs
                # ~50 ms, so sleeping a further 50 ms halved the rate to ~10 Hz
                # and simulated time advanced at half real time - which makes
                # Autoware's trajectory rate checks time out
                # ("Subscribed trajectory is timed out") and autonomous mode
                # never becomes available.
                remaining = self.period - (time.time() - started)
                if remaining > 0:
                    time.sleep(remaining)

        self._thread = threading.Thread(target=_worker, name="tick-pump", daemon=True)
        self._thread.start()
        log.info("[autoware] tick pump started")

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=5.0)
        self._thread = None
        log.info("[autoware] tick pump stopped - Scenic now owns the clock")

    def __enter__(self) -> "TickPump":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def _carla_to_autoware(tf: carla.Transform) -> Dict[str, float]:
    from runner.goal_planner import carla_to_autoware_pose

    return carla_to_autoware_pose(tf)



# Parameter names a Scenic scenario may use for the ego's target speed, in
# preference order. Scenarios that sample it (VerifaiRange(6, 11) and the like)
# are declaring how fast the scene expects the ego to move, so it is also the
# right speed to plan the goal distance against.
_EGO_SPEED_PARAMS = (
    "EGO_SPEED", "ego_speed", "EGO_TARGET_SPEED", "TARGET_SPEED", "EGO_VELOCITY",
)


def scene_ego_speed(scene: Any) -> Optional[float]:
    """The ego target speed a sampled scene declares, in m/s, if any.

    Returns None when the scenario does not declare one, in which case the
    caller falls back to a configured default rather than guessing.
    """
    params = getattr(scene, "params", None) or {}
    for key in _EGO_SPEED_PARAMS:
        if key in params:
            try:
                speed = float(params[key])
            except (TypeError, ValueError):
                continue
            if speed > 0:
                return speed
    return None


def scene_ego_transform(scene: Any, world: carla.World) -> carla.Transform:
    """CARLA transform of the ego in a freshly sampled Scenic scene.

    Scenic has its own coordinate convention, so the conversion goes through
    Scenic's own helpers rather than being reimplemented here - this is the
    third frame in play (Scenic, CARLA, Autoware) and the one place it is
    cheap to get right by delegation.
    """
    # utils is a package; the conversion helpers live in its utils module.
    from scenic.simulators.carla.utils import utils as carla_utils

    ego = scene.egoObject
    loc = carla_utils.scenicToCarlaLocation(
        ego.position,
        world=world,
        blueprint=getattr(ego, "blueprint", None),
        snapToGround=True,
    )
    rot = carla_utils.scenicToCarlaRotation(ego.orientation)
    return carla.Transform(loc, rot)


# --- THE PATCHES -------------------------------------------------------------
@contextlib.contextmanager
def autoware_ownership(session: AutowareSession):
    """Make Scenic defer to Autoware for the map, the ego, and cleanup.

    Six interception points, each undone on exit:

    1. Client.load_world      - Autoware already loaded the map. Reloading it
                                would destroy Autoware's ego and its sensors,
                                and reloading the *same* map is also a known
                                CARLA crash.
    2. createObjectInSimulator- bind the scene's ego to Autoware's existing
                                actor instead of spawning a second car.
    3. Vehicle.apply_control  - swallow any control aimed at the ego, so the
                                .scenic behaviour cannot fight Autoware for the
                                steering wheel. Patched at the CARLA level
                                rather than in executeActions because Scenic's
                                driving actions call vehicle.apply_control()
                                directly (actions.py:69, 82, 88) as well as
                                through the per-step accumulator - guarding
                                only one of those paths would miss the others.
                                NPC control is untouched.
    4. CarlaSimulation.step   - pace each tick to real time, so Autoware can
                                keep up (see _step).
    5. CarlaSimulation.destroy- do not destroy Autoware's ego at end of run.
    6. CarlaSimulator.destroy - do not drop the world out of synchronous mode
                                between runs.
    """
    from scenic.simulators.carla.simulator import CarlaSimulation, CarlaSimulator

    orig_load_world = carla.Client.load_world
    orig_create = CarlaSimulation.createObjectInSimulator
    orig_apply_control = carla.Vehicle.apply_control
    orig_step = CarlaSimulation.step
    orig_sim_destroy = CarlaSimulation.destroy
    orig_simulator_destroy = CarlaSimulator.destroy

    def _load_world(client, map_name, *args, **kwargs):
        current = client.get_world().get_map().name.split("/")[-1]
        if map_name.split("/")[-1] == current:
            log.info(
                "[autoware] not reloading '%s' - Autoware owns the world "
                "(a reload would destroy its ego, and reloading the same map "
                "crashes CARLA)",
                map_name,
            )
            return client.get_world()
        raise AutowareSessionError(
            f"scenario wants map '{map_name}' but Autoware is running '{current}'. "
            "Autoware is bound to one map at launch - relaunch it with the "
            "matching map_path, or run this scenario with --engine behavior_agent."
        )

    def _create(self, obj):
        if obj is getattr(self.scene, "egoObject", None):
            actor = session.ego_actor or find_ego_actor(self.world)
            obj.carlaActor = actor
            log.info("[autoware] ego bound to Autoware's actor id=%d", actor.id)
            return actor
        return orig_create(self, obj)

    def _apply_control(vehicle, control):
        ego = session.ego_actor
        if ego is not None and vehicle.id == ego.id:
            return  # Autoware drives this one
        return orig_apply_control(vehicle, control)


    def _step(self):
        started = time.time()
        result = orig_step(self)
        # Pace the simulation to real time. Without this Scenic ticks as fast
        # as the hardware allows (measured: 2.1x real time), and Autoware -
        # whose computation costs real seconds - cannot keep up, so the ego
        # crawls a metre instead of driving its route.
        remaining = session.step_period_s - (time.time() - started)
        if remaining > 0:
            time.sleep(remaining)
        return result

    def _sim_destroy(self):
        ego = getattr(self.scene, "egoObject", None)
        removed = None
        if ego is not None and ego in self.objects:
            removed = ego
            self.objects = [o for o in self.objects if o is not ego]
        try:
            orig_sim_destroy(self)
        finally:
            if removed is not None:
                self.objects.append(removed)

    def _simulator_destroy(self):
        # Skip the base implementation entirely: it clears synchronous_mode and
        # fixed_delta_seconds, which between runs would leave the world
        # free-running while Autoware is still attached to it.
        self.tm.set_synchronous_mode(False)

    carla.Client.load_world = _load_world
    CarlaSimulation.createObjectInSimulator = _create
    carla.Vehicle.apply_control = _apply_control
    CarlaSimulation.step = _step
    CarlaSimulation.destroy = _sim_destroy
    CarlaSimulator.destroy = _simulator_destroy
    try:
        yield
    finally:
        carla.Client.load_world = orig_load_world
        CarlaSimulation.createObjectInSimulator = orig_create
        carla.Vehicle.apply_control = orig_apply_control
        CarlaSimulation.step = orig_step
        CarlaSimulation.destroy = orig_sim_destroy
        CarlaSimulator.destroy = orig_simulator_destroy
