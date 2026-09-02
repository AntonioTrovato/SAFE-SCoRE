"""
autoware_control.py

Drives Autoware from SAFE-SCoRE: everything the operator does by hand in RViz
(set the initial pose, set a goal, engage) plus the reset needed between runs,
issued as ROS 2 calls instead of clicks.

SAFE-SCoRE runs on Windows; Autoware runs inside WSL. Rather than requiring a
ROS 2 client on the Windows side, each call is shelled out through
`wsl.exe -e bash -lic "... ros2 ..."`. A call costs about a second and a run
needs roughly five of them, which is negligible against a 60 s scenario and
avoids standing up any extra infrastructure.

Resetting between runs matters for throughput: Autoware boots once per suite
(~1 min), and each subsequent run is a handful of service calls rather than a
relaunch.

Standalone use, with CARLA + Autoware already running:

    python src/runner/autoware_control.py --probe
    python src/runner/autoware_control.py --drive-to --x -143.2 --y 142.4 --yaw 1.57 \
        --from-x -158.2 --from-y -2.9 --from-yaw 0.0
"""

from __future__ import annotations

import argparse
import logging
import math
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

EgoDynamicsLike = Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
for _p in (REPO_ROOT, SRC_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

log = logging.getLogger(__name__)

# Sourced before every ros2 invocation. A non-interactive `bash -c` would not
# read .bashrc, so the DDS settings that live there are set explicitly here -
# without them the call joins a different DDS partition and silently sees no
# Autoware nodes at all.
_ROS_PREAMBLE = (
    "source /opt/ros/humble/setup.bash >/dev/null 2>&1; "
    "source $HOME/autoware/install/setup.bash >/dev/null 2>&1; "
    "export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp; "
    "export CYCLONEDDS_URI=file://$HOME/cyclonedds.xml; "
)


def yaw_to_quaternion(yaw_rad: float) -> Dict[str, float]:
    """Yaw about z -> quaternion. Roll and pitch are always zero for a goal
    pose on the road surface."""
    return {"x": 0.0, "y": 0.0, "z": math.sin(yaw_rad / 2.0), "w": math.cos(yaw_rad / 2.0)}


@dataclass
class CallResult:
    ok: bool
    stdout: str
    stderr: str

    @property
    def text(self) -> str:
        return (self.stdout + "\n" + self.stderr).strip()


class RoutingServiceDown(RuntimeError):
    """Raised when the routing service stops answering entirely.

    Distinct from a refused goal: a refusal is normal and worth retrying,
    whereas no answer at all means the node is gone and only a restart
    helps.
    """


class AutowareController:
    """Thin wrapper over the Autoware AD API, reached through WSL."""

    def __init__(
        self,
        distro: str = "Ubuntu-22.04",
        timeout_s: float = 30.0,
        wsl_exe: str = "wsl.exe",
    ) -> None:
        self.distro = distro
        self.timeout_s = timeout_s
        self.wsl_exe = wsl_exe

    # --- plumbing ---------------------------------------------------------
    def _run(self, ros_cmd: str, timeout_s: Optional[float] = None) -> CallResult:
        """Run one ros2 command inside WSL with the environment sourced."""
        full = _ROS_PREAMBLE + ros_cmd
        try:
            proc = subprocess.run(
                [self.wsl_exe, "-d", self.distro, "-e", "bash", "-lic", full],
                capture_output=True,
                text=True,
                timeout=timeout_s or self.timeout_s,
            )
        except subprocess.TimeoutExpired:
            return CallResult(
                False, "", f"SERVICE_TIMEOUT: timed out after {timeout_s or self.timeout_s}s"
            )
        return CallResult(proc.returncode == 0, proc.stdout or "", proc.stderr or "")

    @staticmethod
    def _reason(res: CallResult) -> str:
        """The service's own explanation, not the echoed request.

        `ros2 service call` prints the request before the response, and the
        request for a pose is long enough to push the interesting part out of
        any tail slice - so pull the message field out explicitly.
        """
        m = re.findall(r"message='([^']*)'", res.text)
        return m[-1] if m else res.text[-200:].replace("\n", " ")

    @staticmethod
    def _succeeded(res: CallResult) -> bool:
        """AD API services answer with a ResponseStatus whose `success` field
        is authoritative - the process exit code is 0 even when the service
        rejects the request."""
        return res.ok and "success=True" in res.text.replace(" ", "")

    # --- readiness --------------------------------------------------------
    def wait_ready(self, timeout_s: float = 180.0, poll_s: float = 5.0) -> bool:
        """Block until Autoware answers on the routing API.

        Probing the routing API rather than, say, the bridge's own topics is
        deliberate: it only responds once planning is up, which means the whole
        stack is alive rather than just the CARLA connection.
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            res = self._run(
                "timeout 8 ros2 topic echo /api/routing/state --once", timeout_s=20.0
            )
            if "state:" in res.text:
                log.info("Autoware is up (routing API responding)")
                return True
            time.sleep(poll_s)
        log.error("Autoware did not become ready within %.0fs", timeout_s)
        return False

    # --- introspection ----------------------------------------------------
    _SMOOTHER = "/planning/scenario_planning/velocity_smoother"

    def _get_param(self, node: str, name: str) -> Optional[float]:
        res = self._run(f"timeout 10 ros2 param get {node} {name}", timeout_s=25.0)
        for line in res.text.splitlines():
            if "value is:" in line:
                try:
                    return float(line.split(":")[-1].strip())
                except ValueError:
                    return None
        return None

    def get_dynamics(self, fallback: Optional["EgoDynamicsLike"] = None):
        """Read the limits Autoware will *actually* drive at.

        The nominal vehicle model is not what governs the goal distance:
        Autoware's velocity_smoother caps speed at max_vel (4.17 m/s on this
        setup) and acceleration at normal.max_acc (1.0 m/s^2), well under what
        the vehicle could physically do. Budgeting from the nominal model
        overestimates reachable distance by about 2x, putting the goal out of
        reach for the whole scenario.

        Returns an EgoDynamics with the queried values, falling back to the
        nominal model for anything Autoware does not expose.
        """
        from runner.goal_planner import EgoDynamics  # local: avoids a cycle

        base = fallback or EgoDynamics()
        v_max = self._get_param(self._SMOOTHER, "max_vel")
        a_max = self._get_param(self._SMOOTHER, "normal.max_acc")
        a_min = self._get_param(self._SMOOTHER, "normal.min_acc")

        if v_max is None or a_max is None:
            log.warning(
                "could not read Autoware's velocity limits; falling back to the "
                "nominal model (goal distances will be optimistic)"
            )
            return base

        log.info("Autoware limits: v_max=%.2f m/s a_max=%.2f m/s^2", v_max, a_max)
        return EgoDynamics(
            v_lon_min=base.v_lon_min,
            v_lon_max=v_max,
            v_lat_min=base.v_lat_min,
            v_lat_max=base.v_lat_max,
            v_max=v_max,
            a_lon_min=a_min if a_min is not None else base.a_lon_min,
            a_lon_max=a_max,
            a_lat_min=base.a_lat_min,
            a_lat_max=base.a_lat_max,
            a_max=a_max,
        )

    def set_velocity_limit(self, max_velocity_mps: float) -> bool:
        """Set the speed Autoware will plan up to, at runtime.

        Autoware ships a conservative 4.17 m/s (15 km/h) default, but Scenic
        scenarios sample the ego's target speed themselves (e.g.
        VerifaiRange(6, 11)) and run NPCs at those speeds. Leaving the default
        in place makes the ego permanently the slowest thing on the road, so
        the overtakes and lane changes a scenario is built around can never
        happen - the scenario silently stops testing what it was written to
        test.

        Published rather than configured so it can follow each sampled scene,
        and so Autoware's own config file is left untouched.
        """
        res = self._run(
            "ros2 topic pub --once /planning/scenario_planning/max_velocity_default "
            "autoware_internal_planning_msgs/msg/VelocityLimit "
            f'"{{stamp: {{sec: 0, nanosec: 0}}, max_velocity: {max_velocity_mps:.3f}, '
            'use_constraints: false, sender: safe_score}"',
            timeout_s=25.0,
        )
        if not res.ok:
            log.warning("could not set velocity limit: %s", self._reason(res))
            return False
        log.info("[autoware] velocity limit set to %.2f m/s", max_velocity_mps)
        return True

    def nodes_alive(self, *names: str, timeout_s: float = 15.0) -> bool:
        """Whether the named nodes are still registered.

        Deliberately not a topic check: most Autoware topics are published on
        sim-time timers, and between runs the world is not ticking, so a
        healthy stack looks silent. Node registration is independent of the
        simulation clock, so this stays truthful while the world is frozen -
        which is exactly when the between-runs health check runs.
        """
        res = self._run("timeout 12 ros2 node list", timeout_s=timeout_s + 10.0)
        if not res.text.strip():
            return False
        return all(n in res.text for n in names)

    def planning_alive(self, timeout_s: float = 12.0) -> bool:
        """Whether the planning stack is actually emitting a trajectory.

        The routing API answering only proves the stack started. Autoware's
        motion_planning_container has been seen to segfault mid-session, after
        which routes are still accepted and modes still reported - but no
        trajectory is ever published, so nothing can engage and every run
        yields a stationary ego. This is the check that catches that.
        """
        res = self._run(
            f"timeout {int(timeout_s)} ros2 topic hz "
            "/planning/scenario_planning/trajectory",
            timeout_s=timeout_s + 12.0,
        )
        return "average rate" in res.text

    def routing_state(self) -> Optional[int]:
        """1 = UNSET (no route), 2 = SET, 3 = ARRIVED."""
        res = self._run("timeout 8 ros2 topic echo /api/routing/state --once", timeout_s=20.0)
        for line in res.text.splitlines():
            if line.strip().startswith("state:"):
                try:
                    return int(line.split(":")[1].strip())
                except ValueError:
                    return None
        return None

    def localization_state(self) -> Optional[int]:
        """3 = INITIALIZED."""
        res = self._run(
            "timeout 8 ros2 topic echo /localization/initialization_state --once",
            timeout_s=20.0,
        )
        for line in res.text.splitlines():
            if line.strip().startswith("state:"):
                try:
                    return int(line.split(":")[1].strip())
                except ValueError:
                    return None
        return None

    # --- per-run actions --------------------------------------------------
    @staticmethod
    def _pose_yaml(pose: Dict[str, float]) -> str:
        q = yaw_to_quaternion(pose["yaw"])
        return (
            "{header: {frame_id: map}, pose: {pose: {"
            f"position: {{x: {pose['x']:.4f}, y: {pose['y']:.4f}, z: {pose.get('z', 0.0):.4f}}}, "
            f"orientation: {{x: {q['x']:.6f}, y: {q['y']:.6f}, z: {q['z']:.6f}, w: {q['w']:.6f}}}"
            "}}}"
        )

    def publish_initial_pose(self, pose: Dict[str, float]) -> bool:
        """Teleport the CARLA ego to `pose` (Autoware frame).

        This is the half that physically moves the car: the bridge's
        initialpose_callback calls ego_actor.set_transform. Note it also does
        `position.z += 2.0`, so the car is *dropped* from two metres up and is
        briefly in free fall - see AutowareSession.prepare, which waits for it
        to settle before initializing localization.
        """
        res = self._run(
            "ros2 topic pub --once /initialpose "
            f'geometry_msgs/msg/PoseWithCovarianceStamped "{self._pose_yaml(pose)}"',
            timeout_s=25.0,
        )
        if not res.ok:
            log.error("publishing /initialpose failed: %s", res.text[-400:])
            return False
        return True

    def vehicle_speed(self) -> Optional[float]:
        """Longitudinal speed as *Autoware* sees it (m/s).

        Not the same as reading the CARLA actor's velocity: this value arrives
        through the bridge's sensor loop and therefore lags the simulation. It
        is the value /api/localization/initialize checks when it decides
        whether the vehicle is stopped, so it is the one worth waiting on.
        """
        res = self._run(
            "timeout 8 ros2 topic echo /vehicle/status/velocity_status --once", timeout_s=20.0
        )
        for line in res.text.splitlines():
            if line.strip().startswith("longitudinal_velocity:"):
                try:
                    return abs(float(line.split(":")[1].strip()))
                except ValueError:
                    return None
        return None

    def initialize_localization(self, pose: Dict[str, float], attempts: int = 3) -> bool:
        """Tell Autoware's localization where the ego now is.

        Fails with "The vehicle is not stopped." if the car is still moving,
        which after a teleport it is - hence the settle wait in the caller.
        """
        for attempt in range(1, attempts + 1):
            res = self._run(
                "ros2 service call /api/localization/initialize "
                f'autoware_adapi_v1_msgs/srv/InitializeLocalization '
                f'"{{pose: [{self._pose_yaml(pose)}]}}"',
                timeout_s=40.0,
            )
            if self._succeeded(res):
                return True
            if "not stopped" in res.text and attempt < attempts:
                # Autoware's velocity estimate still carries the teleport's
                # free fall; give the bridge another moment to publish zeros.
                log.info(
                    "localization initialize: vehicle still moving, retry %d/%d",
                    attempt, attempts,
                )
                time.sleep(2.0)
                continue
            log.warning("localization initialize failed: %s", self._reason(res))
            return False
        return False

    def set_initial_pose(self, pose: Dict[str, float], settle_s: float = 3.0) -> bool:
        """Teleport + initialize in one call, with a fixed settle delay.

        Kept for standalone/CLI use; the runner uses the two halves separately
        so it can wait on the ego's actual velocity instead of a fixed delay.
        """
        q = yaw_to_quaternion(pose["yaw"])
        pose_yaml = (
            "{header: {frame_id: map}, pose: {pose: {"
            f"position: {{x: {pose['x']:.4f}, y: {pose['y']:.4f}, z: {pose.get('z', 0.0):.4f}}}, "
            f"orientation: {{x: {q['x']:.6f}, y: {q['y']:.6f}, z: {q['z']:.6f}, w: {q['w']:.6f}}}"
            "}}}"
        )
        pub = self._run(
            "ros2 topic pub --once /initialpose "
            f'geometry_msgs/msg/PoseWithCovarianceStamped "{pose_yaml}"',
            timeout_s=25.0,
        )
        if not pub.ok:
            log.error("publishing /initialpose failed: %s", pub.text[-400:])
            return False

        time.sleep(settle_s)  # let the teleport land before localization reads it

        init = self._run(
            "ros2 service call /api/localization/initialize "
            f'autoware_adapi_v1_msgs/srv/InitializeLocalization "{{pose: [{pose_yaml}]}}"',
            timeout_s=40.0,
        )
        if not self._succeeded(init):
            log.warning("localization initialize did not report success: %s", init.text[-400:])
        return True

    def set_goal(self, goal: Dict[str, float], allow_modification: bool = True) -> bool:
        """Set the navigation goal (Autoware frame).

        allow_goal_modification lets Autoware nudge a goal that does not quite
        fit the lane, instead of rejecting it outright with "Goal's footprint
        exceeds lane!". Worth keeping on: the goal comes from a random walk
        over CARLA waypoints, which will not always agree perfectly with the
        lanelet map's idea of where a lane centre is.
        """
        q = yaw_to_quaternion(goal["yaw"])
        args = (
            "{header: {frame_id: map}, "
            f"option: {{allow_goal_modification: {str(allow_modification).lower()}}}, "
            f"goal: {{position: {{x: {goal['x']:.4f}, y: {goal['y']:.4f}, z: {goal.get('z', 0.0):.4f}}}, "
            f"orientation: {{x: {q['x']:.6f}, y: {q['y']:.6f}, z: {q['z']:.6f}, w: {q['w']:.6f}}}}}, "
            "waypoints: []}"
        )
        res = self._run(
            "ros2 service call /api/routing/set_route_points "
            f'autoware_adapi_v1_msgs/srv/SetRoutePoints "{args}"',
            timeout_s=40.0,
        )
        if not self._succeeded(res):
            if "SERVICE_TIMEOUT" in res.text:
                # No server answered. The mission planner container has been
                # seen to segfault mid-suite; once it does, every later call
                # times out and retrying is pointless.
                raise RoutingServiceDown(
                    "set_route_points did not answer - Autoware's mission planner "
                    "is very likely dead (check the launch terminal for "
                    "'mission_planner_container ... process has died')."
                )
            log.warning("set_route_points rejected the goal: %s", self._reason(res))
            return False
        return True

    def wait_engageable(self, timeout_s: float = 60.0, poll_s: float = 3.0) -> bool:
        """Block until autonomous mode is actually offered.

        Setting a route does not make the stack immediately engageable: planning
        still has to produce a trajectory and the control chain has to start
        publishing before /api/operation_mode/state flips
        is_autonomous_mode_available to true. Engaging before that returns
        "The target mode is not available. Please check the diagnostics." -
        which reads like a real fault but is only a race.
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            res = self._run(
                "timeout 8 ros2 topic echo /api/operation_mode/state --once", timeout_s=20.0
            )
            if "is_autonomous_mode_available: true" in res.text:
                return True
            time.sleep(poll_s)
        log.error("autonomous mode never became available within %.0fs", timeout_s)
        return False

    def engage(self, wait_s: float = 60.0) -> bool:
        if wait_s and not self.wait_engageable(timeout_s=wait_s):
            return False
        res = self._run(
            "ros2 service call /api/operation_mode/change_to_autonomous "
            'autoware_adapi_v1_msgs/srv/ChangeOperationMode "{}"',
            timeout_s=30.0,
        )
        if not self._succeeded(res):
            log.error("engage failed: %s", self._reason(res))
            return False
        return True

    def stop(self) -> bool:
        res = self._run(
            "ros2 service call /api/operation_mode/change_to_stop "
            'autoware_adapi_v1_msgs/srv/ChangeOperationMode "{}"',
            timeout_s=30.0,
        )
        return self._succeeded(res)

    def clear_route(self) -> bool:
        res = self._run(
            "ros2 service call /api/routing/clear_route "
            'autoware_adapi_v1_msgs/srv/ClearRoute "{}"',
            timeout_s=30.0,
        )
        return self._succeeded(res)

    def reset_for_next_run(self) -> bool:
        """Return Autoware to a clean state without relaunching it."""
        ok_stop = self.stop()
        ok_clear = self.clear_route()
        log.info("reset: stop=%s clear_route=%s", ok_stop, ok_clear)
        return ok_stop and ok_clear


# --- CLI ---------------------------------------------------------------------
def main() -> None:
    p = argparse.ArgumentParser(description="Drive Autoware from the command line.")
    p.add_argument("--distro", default="Ubuntu-22.04")
    p.add_argument("--probe", action="store_true", help="Check Autoware is up and report state")
    p.add_argument("--reset", action="store_true", help="Stop and clear the current route")
    p.add_argument("--drive-to", action="store_true", help="Init pose, set goal, engage")
    p.add_argument("--x", type=float, help="Goal x (Autoware frame)")
    p.add_argument("--y", type=float, help="Goal y (Autoware frame)")
    p.add_argument("--yaw", type=float, default=0.0, help="Goal yaw (rad)")
    p.add_argument("--from-x", type=float, help="Start x (Autoware frame)")
    p.add_argument("--from-y", type=float, help="Start y (Autoware frame)")
    p.add_argument("--from-yaw", type=float, default=0.0, help="Start yaw (rad)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    aw = AutowareController(distro=args.distro)

    if args.probe:
        print("waiting for Autoware...")
        if not aw.wait_ready(timeout_s=60.0):
            sys.exit(1)
        print(f"  routing state      = {aw.routing_state()}   (1=UNSET 2=SET 3=ARRIVED)")
        print(f"  localization state = {aw.localization_state()}   (3=INITIALIZED)")
        return

    if args.reset:
        print("reset:", "ok" if aw.reset_for_next_run() else "FAILED")
        return

    if args.drive_to:
        if args.x is None or args.y is None:
            p.error("--drive-to needs --x and --y")
        if args.from_x is not None and args.from_y is not None:
            print("setting initial pose...")
            if not aw.set_initial_pose(
                {"x": args.from_x, "y": args.from_y, "z": 0.0, "yaw": args.from_yaw}
            ):
                sys.exit(1)
            print(f"  localization state = {aw.localization_state()}")
        print("setting goal...")
        if not aw.set_goal({"x": args.x, "y": args.y, "z": 0.0, "yaw": args.yaw}):
            sys.exit(1)
        print(f"  routing state = {aw.routing_state()}  (2 = SET)")
        print("engaging...")
        print("  engage:", "ok" if aw.engage() else "FAILED")
        return

    p.print_help()


if __name__ == "__main__":
    main()
