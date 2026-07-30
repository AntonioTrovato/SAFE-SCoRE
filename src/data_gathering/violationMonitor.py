from typing import TYPE_CHECKING, Dict, List

import carla

if TYPE_CHECKING:
    from carlaBasicLogger import CarlaBasicLogger

from utils.carla_help import get_speed_mps, get_speed_kmh


class ViolationMonitor:
    """
    Monitors, in real time, a set of the ego vehicle's behavioral violations
    during the simulation. Logs events directly on the associated logger.

    Currently supports:
    - red-light crossings
    - speed-limit violations
    - stop-sign violations
    """

    def __init__(self, ego_vehicle: carla.Vehicle, logger: "CarlaBasicLogger"):
        """
        Initializes the violation-monitoring system.

        Args:
            ego_vehicle: the controlled (ego) vehicle
            logger: the associated CarlaBasicLogger instance
        """
        self.ego_vehicle = ego_vehicle
        self.logger = logger
        self.world = self.logger.world
        self.map = self.world.get_map()
        self.fps = int(1 / self.world.get_settings().fixed_delta_seconds)

        # ---------------------------------------------------------------------
        # Integration with the logger's JSON structure
        # ---------------------------------------------------------------------
        sd = self.logger.scenario_data
        # results block
        self.results: Dict = sd.setdefault("results", {})
        self.results.setdefault("has_speeding", False)
        self.results.setdefault("has_red_light_violation", False)
        self.results.setdefault("has_stop_violation", False)

        # events block
        self.events: Dict = sd.setdefault("events", {})
        self.events.setdefault("speeding", [])
        self.events.setdefault("red_lights", [])
        self.events.setdefault("stop_sign", [])

        #######################################################################
        # Configuration parameters
        #######################################################################
        # General parameters
        self.min_speed_threshold_mps = 0.3  # minimum speed (mps) below which the vehicle is considered stopped

        #######################################################################
        # Speeding
        self.last_speed_limit = None  # last detected speed limit (km/h)
        self.last_speeding_frame = -1  # frame at which the last speeding violation was detected
        self.frame_speed_lim_changed = -1  # frame at which the speed limit changed
        self.speed_grace_T = 3  # grace period (seconds) after a speed-limit change
        self.speed_margin_kmh = 5.0  # tolerance margin over the limit (km/h) before flagging a violation

        #######################################################################
        # Red light
        self.on_red = False
        self.on_red_speeds: List[float] = []
        # traffic light id -> last violation timestamp
        self.red_light_violations_timestamps: Dict[int, float] = {}
        self.red_light_cooldown_s = 10.0  # minimum time (seconds) before reconsidering the same traffic light

        #######################################################################
        # Stop signs
        self.stop_signs: List[carla.TrafficSign] = self.world.get_actors().filter("*stop*")
        self.stop_proximity_threshold = 15.0  # m
        self.stop_required_duration = 2.0  # s
        self.stop_cooldown_s = 5.0  # minimum time between two violations at the same stop sign

        # Tracks the stop state for each stop sign
        # {stop_id: {"stopped_time": float|None, "was_stopped": bool, "location": carla.Location, "last_violation_time": float}}
        self.stop_states: Dict[int, Dict] = {}
        self.current_stop_violations = set()  # ids of stops already violated this frame

        self.is_off_road = False


    # -------------------------------------------------------------------------
    # MAIN TICK
    # -------------------------------------------------------------------------

    def tick(self, frame: int, timestamp: float):
        """
        Called once per simulation frame. Checks all currently enabled violations.
        """
        self.check_speeding(frame, timestamp)
        self.check_red_light(frame, timestamp)
        self.check_stop_signs(frame, timestamp)
        self.check_off_road(frame, timestamp)

    # -------------------------------------------------------------------------
    # SPEEDING
    # -------------------------------------------------------------------------

    def check_speeding(self, frame: int, timestamp: float):
        """
        Checks whether the vehicle has exceeded the current speed limit.
        """
        speed_kmh = get_speed_kmh(self.ego_vehicle)
        speed_limit_kmh = self.ego_vehicle.get_speed_limit()

        # 1. limit changed: update
        if self.last_speed_limit != speed_limit_kmh:
            self.frame_speed_lim_changed = frame
            self.last_speed_limit = speed_limit_kmh

        # 2. grace period after a limit change
        grace_frames = int(self.speed_grace_T * self.fps)
        if frame < self.frame_speed_lim_changed + grace_frames:
            return  # don't check yet

        # 3. check speeding
        if speed_kmh > speed_limit_kmh + self.speed_margin_kmh:
            if frame != self.last_speeding_frame:
                # log on the "historical" logger
                self.logger.log_speeding(frame, timestamp, speed_kmh, speed_limit_kmh)

                # structured log for SOTIF / risk_enrichment
                self.results["has_speeding"] = True
                self.events["speeding"].append(
                    {
                        "frame": frame,
                        "timestamp": timestamp,
                        "speed_kmh": speed_kmh,
                        "speed_limit_kmh": speed_limit_kmh,
                    }
                )

                self.last_speeding_frame = frame

    # -------------------------------------------------------------------------
    # RED LIGHT
    # -------------------------------------------------------------------------

    def check_red_light(self, frame: int, timestamp: float):
        lights = self.ego_vehicle.get_traffic_light()
        at_traffic_light = self.ego_vehicle.is_at_traffic_light()

        if lights is not None and lights.state == carla.TrafficLightState.Red and at_traffic_light:
            # we're inside a red traffic light's trigger zone
            speed = get_speed_kmh(self.ego_vehicle)
            if not self.on_red:
                self.on_red = True
                self.on_red_speeds = []
            self.on_red_speeds.append(speed)

        # left the trigger box, regardless of the light's color
        elif not at_traffic_light and self.on_red:
            # decide whether a violation occurred
            self.on_red = False
            if self.on_red_speeds and all(s > 0.1 for s in self.on_red_speeds):
                tl_id = lights.id if lights else -1
                last_violation_time = self.red_light_violations_timestamps.get(tl_id, -999.0)

                if timestamp - last_violation_time > self.red_light_cooldown_s:
                    max_speed = max(self.on_red_speeds)

                    # "historical" log
                    self.logger.log_red_light(frame, timestamp, max_speed)

                    # structured log
                    self.results["has_red_light_violation"] = True
                    self.events["red_lights"].append(
                        {
                            "frame": frame,
                            "timestamp": timestamp,
                            "max_speed_kmh": max_speed,
                            "traffic_light_id": tl_id,
                        }
                    )

                    self.red_light_violations_timestamps[tl_id] = timestamp

    # -------------------------------------------------------------------------
    # STOP SIGNS
    # -------------------------------------------------------------------------

    def check_stop_signs(self, frame: int, timestamp: float):
        """
        Checks for stop-sign-related violations.
        """
        ego_location = self.ego_vehicle.get_location()
        ego_speed_mps = get_speed_mps(self.ego_vehicle)
        is_ego_stopped = ego_speed_mps <= self.min_speed_threshold_mps

        # 1. identify the stops currently "active" (the ones the ego is inside)
        active_stop_ids = set()
        for stop_sign in self.stop_signs:
            if ego_location.distance(stop_sign.get_location()) < self.stop_proximity_threshold:
                trigger_volume: carla.BoundingBox = stop_sign.trigger_volume
                if trigger_volume.contains(ego_location, stop_sign.get_transform()):
                    active_stop_ids.add(stop_sign.id)

        # 2. judge the stops that are no longer active (the vehicle just left them)
        stops_to_remove = []
        for stop_id, state in self.stop_states.items():
            if stop_id not in active_stop_ids:
                # the vehicle left the trigger zone, time to judge the encounter
                if not state.get("was_stopped", False):
                    last_time = state.get("last_violation_time", -999.0)
                    if timestamp - last_time > self.stop_cooldown_s:
                        speed_kmh = get_speed_kmh(self.ego_vehicle)

                        # historical log
                        self.logger.log_stop_violation(
                            frame=frame,
                            timestamp=timestamp,
                            lm_id=stop_id,
                            lm_loc=state["location"],
                            speed_kmh=speed_kmh,
                            stopped=False,
                        )

                        # structured log
                        self.results["has_stop_violation"] = True
                        self.events["stop_sign"].append(
                            {
                                "frame": frame,
                                "timestamp": timestamp,
                                "stop_sign_id": stop_id,
                                "distance_to_sign": ego_location.distance(state["location"]),
                                "speed_kmh": speed_kmh,
                                "stopped": False,
                            }
                        )

                        # store the violation timestamp for the cooldown
                        state["last_violation_time"] = timestamp

                # mark it for removal from the state map
                stops_to_remove.append(stop_id)

        # clean up the states of finished encounters
        for stop_id in stops_to_remove:
            if stop_id in self.stop_states:
                del self.stop_states[stop_id]

        # 3. update or create the state for the currently active stops
        for stop_id in active_stop_ids:
            # new encounter
            if stop_id not in self.stop_states:
                stop_actor = self.world.get_actor(stop_id)
                if stop_actor:
                    self.stop_states[stop_id] = {
                        "stopped_time": None,
                        "was_stopped": False,
                        "location": stop_actor.get_location(),
                        "last_violation_time": -999.0,
                    }

            # update the current state
            if stop_id in self.stop_states:
                state = self.stop_states[stop_id]

                # if the ego stops for the first time during this encounter
                if is_ego_stopped and state["stopped_time"] is None:
                    state["stopped_time"] = timestamp

                # if the ego starts moving again, reset the timer
                if not is_ego_stopped:
                    state["stopped_time"] = None

                # if it stopped long enough, the encounter is "compliant"
                if state["stopped_time"] is not None:
                    stop_duration = timestamp - state["stopped_time"]
                    if stop_duration >= self.stop_required_duration:
                        state["was_stopped"] = True

    def check_off_road(self, frame: int, timestamp: float):
        loc = self.ego_vehicle.get_location()
        wp = self.map.get_waypoint(loc, project_to_road=False)

        if wp is None:
            # entered off-road
            if not self.is_off_road:
                self.logger.log_off_road(frame, timestamp, loc)
                self.is_off_road = True
        else:
            # back on the road
            self.is_off_road = False
