"""
goal_planner.py

Computes an Autoware navigation goal for a Scenic scenario.

A .scenic file has no notion of a destination, but Autoware needs one before
it will drive. This module derives a plausible goal from the sampled scene:

    the ego follows its lane; at every junction it picks one of the m outgoing
    branches with probability 1/m; the goal is the furthest point it could
    plausibly reach within the scenario's time limit.

The lane graph comes from CARLA itself (`carla.Map.get_waypoint` /
`Waypoint.next`), not from a SUMO .net.xml: CARLA's waypoints are already in
the simulation's own coordinate frame, whereas the SUMO net carries a
netOffset (and a y-flip), which would add a second coordinate conversion and
with it a second chance of a silently mirrored goal.

The scene is re-sampled on every run (scenic_carla_runner._run_once_worker
calls scenario.generate() per run), so the ego's start pose differs each time
and a goal must be computed per run, not once per scenario.

Standalone use (with a CARLA server running):

    python src/runner/goal_planner.py --samples 10 --time_limit 60
    python src/runner/goal_planner.py --x 100 --y -50 --yaw 90 --time_limit 60
"""

from __future__ import annotations

import argparse
import logging
import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
for _p in (REPO_ROOT, SRC_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import carla  # noqa: E402

log = logging.getLogger(__name__)


# --- EGO DYNAMIC MODEL -------------------------------------------------------
@dataclass(frozen=True)
class EgoDynamics:
    """Longitudinal/lateral limits of the ego vehicle.

    Only v_lon_max and a_lon_max enter the distance budget; the lateral limits
    are kept because they are the physical reason the raw budget is optimistic
    (a_lat_max = 0.7 m/s^2 is restrictive, so corners are taken slowly) and so
    inform the choice of REALISM_FACTOR below.
    """

    v_lon_min: float = 0.0
    v_lon_max: float = 10.0
    v_lat_min: float = -2.0
    v_lat_max: float = 2.0
    v_max: float = 10.0

    a_lon_min: float = -6.0
    a_lon_max: float = 2.0
    a_lat_min: float = -0.7
    a_lat_max: float = 0.7
    a_max: float = 2.0


# Fraction of the straight-line budget actually used as the goal distance.
# The raw figure assumes uninterrupted acceleration then cruise; real driving
# decelerates for junctions and turns.
#
# Measured on Town05 (2026-09-02): with Autoware's own limits the raw budget
# for a 60 s scenario is 241 m and the ego covered ~180 m, i.e. 0.75. Note this
# only holds when `dyn` carries Autoware's limits (see
# AutowareController.get_dynamics), NOT the nominal vehicle model - Autoware
# drives at 4.17 m/s where the vehicle could do 10, so using the nominal model
# overestimates by roughly 2x.
REALISM_FACTOR = 0.75

# Distance between successive waypoint queries along the walk (m). Small
# enough not to step over a junction entrance, large enough to stay cheap.
WALK_STEP_M = 2.0

# After the budget is spent, keep walking at most this far to get clear of a
# junction. Autoware rejects goals whose footprint exceeds the lane, which is
# what a goal placed inside an intersection looks like.
JUNCTION_ESCAPE_M = 30.0


def reachable_distance(time_limit_s: float, dyn: EgoDynamics = EgoDynamics()) -> float:
    """Straight-line distance the ego could cover in `time_limit_s`.

    Accelerate at a_lon_max up to v_lon_max, then cruise:

        t_accel = v_max / a_max            (5.0 s with the default model)
        d_accel = 0.5 * a_max * t_accel^2  (25.0 m)
        d(T)    = d_accel + v_max * (T - t_accel)      for T > t_accel
                = 0.5 * a_max * T^2                    otherwise
    """
    t_accel = dyn.v_lon_max / dyn.a_lon_max
    if time_limit_s <= t_accel:
        return 0.5 * dyn.a_lon_max * time_limit_s**2
    d_accel = 0.5 * dyn.a_lon_max * t_accel**2
    return d_accel + dyn.v_lon_max * (time_limit_s - t_accel)


# --- COORDINATE CONVERSION ---------------------------------------------------
def carla_to_autoware_pose(transform: carla.Transform) -> Dict[str, float]:
    """CARLA transform -> Autoware/ROS pose (metres, radians).

    Autoware's maps for CARLA are the *y-axis inverted* variants, because ROS
    is right-handed and CARLA is left-handed. Matches
    CoordinateTransformer.carla_transform_to_ros_transform in
    autoware_carla_interface: x and roll unchanged, y/pitch/yaw negated.

    Getting this wrong does not raise - Autoware simply plans routes mirrored
    across the map - so it lives in exactly one place, here.
    """
    loc, rot = transform.location, transform.rotation
    return {
        "x": loc.x,
        "y": -loc.y,
        "z": loc.z,
        "roll": math.radians(rot.roll),
        "pitch": math.radians(-rot.pitch),
        "yaw": math.radians(-rot.yaw),
    }


# --- THE WALK ----------------------------------------------------------------
@dataclass
class GoalResult:
    """Outcome of one goal computation."""

    goal_transform: carla.Transform
    goal_autoware: Dict[str, float]
    distance_m: float
    budget_m: float
    junctions_taken: int
    branch_counts: List[int]
    steps: int
    terminated_early: bool
    reason: str

    def summary(self) -> str:
        loc = self.goal_transform.location
        return (
            f"goal=({loc.x:.1f}, {loc.y:.1f}) carla / "
            f"({self.goal_autoware['x']:.1f}, {self.goal_autoware['y']:.1f}) autoware  "
            f"dist={self.distance_m:.0f}m/{self.budget_m:.0f}m  "
            f"junctions={self.junctions_taken}{self.branch_counts}  "
            f"{'EARLY: ' + self.reason if self.terminated_early else self.reason}"
        )


def compute_goal(
    carla_map: carla.Map,
    start_transform: carla.Transform,
    time_limit_s: float,
    *,
    dyn: EgoDynamics = EgoDynamics(),
    realism_factor: float = REALISM_FACTOR,
    step_m: float = WALK_STEP_M,
    rng: Optional[random.Random] = None,
) -> GoalResult:
    """Walk the lane graph forward from `start_transform` and return a goal.

    `rng` makes a run reproducible: pass a Random seeded from (scenario_id,
    run_index) and the same scene yields the same goal.
    """
    rng = rng or random.Random()
    budget = realism_factor * reachable_distance(time_limit_s, dyn)

    wp = carla_map.get_waypoint(
        start_transform.location, project_to_road=True, lane_type=carla.LaneType.Driving
    )
    if wp is None:
        raise ValueError(
            f"start position ({start_transform.location.x:.1f}, "
            f"{start_transform.location.y:.1f}) does not project onto any driving lane"
        )

    travelled = 0.0
    junctions = 0
    branches: List[int] = []
    steps = 0
    early = False
    reason = "budget reached"

    while travelled < budget:
        nxt = wp.next(step_m)
        if not nxt:
            early, reason = True, "dead end - no successor lane"
            break
        if len(nxt) > 1:
            junctions += 1
            branches.append(len(nxt))
        wp = rng.choice(nxt)  # uniform 1/m over the branches
        travelled += step_m
        steps += 1

    # A goal inside a junction is rejected by Autoware ("Goal's footprint
    # exceeds lane!"), so step clear of one before stopping.
    escaped = 0.0
    while wp.is_junction and escaped < JUNCTION_ESCAPE_M:
        nxt = wp.next(step_m)
        if not nxt:
            break
        wp = rng.choice(nxt)
        travelled += step_m
        escaped += step_m
    if wp.is_junction:
        early, reason = True, "could not clear junction"
    elif escaped:
        reason = f"{reason} (+{escaped:.0f}m to clear junction)"

    goal_tf = wp.transform
    return GoalResult(
        goal_transform=goal_tf,
        goal_autoware=carla_to_autoware_pose(goal_tf),
        distance_m=travelled,
        budget_m=budget,
        junctions_taken=junctions,
        branch_counts=branches,
        steps=steps,
        terminated_early=early,
        reason=reason,
    )


# --- CLI ---------------------------------------------------------------------
def _sample_starts(carla_map: carla.Map, n: int, rng: random.Random) -> List[carla.Transform]:
    spawn_points = carla_map.get_spawn_points()
    return rng.sample(spawn_points, min(n, len(spawn_points)))


def main() -> None:
    p = argparse.ArgumentParser(description="Compute Autoware goals from Scenic ego start poses.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=2000)
    p.add_argument("--time_limit", type=float, default=60.0, help="Scenario duration (s)")
    p.add_argument("--samples", type=int, default=10, help="Random start poses to try")
    p.add_argument("--x", type=float, help="Explicit start x (CARLA frame)")
    p.add_argument("--y", type=float, help="Explicit start y (CARLA frame)")
    p.add_argument("--yaw", type=float, default=0.0, help="Explicit start yaw (deg)")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--realism", type=float, default=REALISM_FACTOR)
    p.add_argument(
        "--use-autoware-limits",
        action="store_true",
        help="Budget from the speed/acceleration Autoware actually drives at, "
        "read live from its velocity_smoother, instead of the nominal vehicle "
        "model. Requires Autoware to be running.",
    )
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    rng = random.Random(args.seed)

    dyn = EgoDynamics()
    if args.use_autoware_limits:
        from runner.autoware_control import AutowareController

        dyn = AutowareController().get_dynamics(fallback=dyn)

    client = carla.Client(args.host, args.port)
    client.set_timeout(30.0)
    world = client.get_world()
    cmap = world.get_map()

    raw = reachable_distance(args.time_limit, dyn)
    budget = args.realism * raw
    print(f"map={cmap.name}  time_limit={args.time_limit:.0f}s")
    print(f"limits: v_max={dyn.v_lon_max:.2f} m/s  a_max={dyn.a_lon_max:.2f} m/s^2")
    print(f"raw budget={raw:.0f}m  x{args.realism} -> goal distance={budget:.0f}m\n")

    if args.x is not None and args.y is not None:
        starts = [
            carla.Transform(
                carla.Location(x=args.x, y=args.y, z=0.5), carla.Rotation(yaw=args.yaw)
            )
        ]
    else:
        starts = _sample_starts(cmap, args.samples, rng)

    ok = 0
    for i, st in enumerate(starts, 1):
        try:
            res = compute_goal(
                cmap, st, args.time_limit, dyn=dyn, realism_factor=args.realism, rng=rng
            )
        except ValueError as exc:
            print(f"{i:2d}. start=({st.location.x:7.1f},{st.location.y:7.1f})  FAILED: {exc}")
            continue
        if not res.terminated_early:
            ok += 1
        # Straight-line vs walked distance: a plausible route is never
        # straighter than the crow flies, so the ratio must be <= 1, and a
        # value near 1 means the walk barely turned.
        gl = res.goal_transform.location
        euclid = math.hypot(gl.x - st.location.x, gl.y - st.location.y)
        ratio = euclid / res.distance_m if res.distance_m else 0.0
        print(
            f"{i:2d}. start=({st.location.x:7.1f},{st.location.y:7.1f})  {res.summary()}"
            f"  straightness={ratio:.2f}"
        )

    print(f"\n{ok}/{len(starts)} reached the full budget without terminating early.")


if __name__ == "__main__":
    main()
