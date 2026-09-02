"""
follow_camera.py

Keeps CARLA's spectator camera pointed at the ego, so a run can be watched.

Purely an observer: it reads the ego's transform and moves the spectator. It
never ticks the world and never touches world settings, so it cannot interfere
with whoever owns the clock - which matters in --engine autoware, where exactly
one client may tick.

Only useful when CARLA has a window, i.e. when it was NOT started with
-RenderOffScreen.

Runs as a background thread inside the runner; it survives across runs and
reconnects by itself when the world is replaced (a CARLA restart, or Autoware
reloading the map).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Optional

import carla

log = logging.getLogger(__name__)

EGO_ROLE_NAME = "ego_vehicle"


def _camera_transform(
    vehicle: carla.Transform, mode: str, offset_back: float, offset_z: float
) -> carla.Transform:
    """Spectator pose relative to the vehicle."""
    yaw_rad = math.radians(vehicle.rotation.yaw)
    loc = vehicle.location

    if mode == "top":
        return carla.Transform(
            carla.Location(x=loc.x, y=loc.y, z=loc.z + offset_z * 3.0),
            carla.Rotation(pitch=-90.0, yaw=vehicle.rotation.yaw),
        )
    if mode == "front":
        return carla.Transform(
            carla.Location(
                x=loc.x + offset_back * math.cos(yaw_rad),
                y=loc.y + offset_back * math.sin(yaw_rad),
                z=loc.z + offset_z,
            ),
            carla.Rotation(pitch=-15.0, yaw=vehicle.rotation.yaw + 180.0),
        )
    # "behind" - chase camera
    return carla.Transform(
        carla.Location(
            x=loc.x - offset_back * math.cos(yaw_rad),
            y=loc.y - offset_back * math.sin(yaw_rad),
            z=loc.z + offset_z,
        ),
        carla.Rotation(pitch=-15.0, yaw=vehicle.rotation.yaw),
    )


class SpectatorFollower:
    """Background thread that keeps the spectator on the ego."""

    def __init__(
        self,
        address: str = "127.0.0.1",
        port: int = 2000,
        mode: str = "behind",
        offset_back: float = 8.0,
        offset_z: float = 4.0,
        rate_hz: float = 30.0,
    ) -> None:
        self.address = address
        self.port = port
        self.mode = mode
        self.offset_back = offset_back
        self.offset_z = offset_z
        self.period = 1.0 / rate_hz
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return

        def _worker() -> None:
            client = None
            world = None
            ego = None
            while not self._stop.is_set():
                try:
                    if client is None:
                        client = carla.Client(self.address, self.port)
                        client.set_timeout(10.0)
                        world = client.get_world()
                        ego = None
                    if ego is None or not ego.is_alive:
                        vehicles = list(world.get_actors().filter("vehicle.*"))
                        # --engine autoware: Autoware's ego carries role_name
                        # 'ego_vehicle'.
                        ego = next(
                            (
                                a
                                for a in vehicles
                                if a.attributes.get("role_name") == EGO_ROLE_NAME
                            ),
                            None,
                        )
                        # --engine behavior_agent: Scenic spawns the ego itself
                        # and sets no such role, so fall back to the
                        # lowest-id vehicle - Scenic creates the ego first, so
                        # it always holds the lowest actor id in the scene.
                        if ego is None and vehicles:
                            ego = min(vehicles, key=lambda a: a.id)
                        if ego is None:
                            # No ego yet (Autoware still starting, or a restart
                            # in progress). Re-resolve the world too, in case it
                            # was replaced under us.
                            time.sleep(1.0)
                            world = client.get_world()
                            continue
                    world.get_spectator().set_transform(
                        _camera_transform(
                            ego.get_transform(), self.mode, self.offset_back, self.offset_z
                        )
                    )
                except RuntimeError:
                    # CARLA restarted, or the actor vanished mid-read. Drop the
                    # connection and pick it up again on the next pass.
                    client = None
                    world = None
                    ego = None
                    time.sleep(2.0)
                    continue
                time.sleep(self.period)

        self._thread = threading.Thread(
            target=_worker, name="spectator-follower", daemon=True
        )
        self._thread.start()
        log.info("Spectator camera is following the ego (mode=%s).", self.mode)

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=3.0)
        self._thread = None
