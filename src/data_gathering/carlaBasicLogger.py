import json
import os
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import carla

from utils.carla_help import analyze_collision

try:
    from violationMonitor import ViolationMonitor
except ImportError:
    from data_gathering.violationMonitor import ViolationMonitor

if TYPE_CHECKING:
    # Only needed for the type hint in set_mission_from_agent(); CARLA's
    # PythonAPI/agents package (where BehaviorAgent lives) isn't a hard
    # runtime dependency of this module.
    from agents.navigation.behavior_agent import BehaviorAgent

LOGGER_REGISTRY: Dict[int, "CarlaBasicLogger"] = {}

class CarlaBasicLogger:
    """
        Logger responsible for saving, for a single scenario execution, a
        JSON file containing all the information needed to perform an
        effective analysis of the scenario.
    """

    def __init__(self, tool: str, generation_id: str, scenario_id: str, output_dir: str,
                world: carla.World, client: carla.Client,
                violation_monitor: Optional[ViolationMonitor] = None,
                delta_time: float = 0.05, record_binary: bool = False, run_index: int = 1):
        """
        Initializes the base logger.
        """
        # metadata
        self.tool = tool
        self.generation_id = generation_id
        self.scenario_id = scenario_id

        self.output_dir = output_dir
        # create the output folder if it doesn't already exist
        os.makedirs(output_dir, exist_ok=True)

        # timing
        self.start_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.simulation_start_time = time.time()
        self._first_timestamp = None
        self.delta_time = delta_time
        # frame counter for the log
        self.frame_count = 0

        self.map_name = "unknown"

        self.world = world
        self.client = client
        self.record_binary = record_binary
        self.run_index = run_index
        self.recorder_path = None

        self.violation_monitor = violation_monitor

        self.last_collision_timestamp = -1  # timestamp of the last detected collision
        self.last_collision_actor_id = -1  # id of the last actor involved in a collision
        self.collision_cooldown = 2  # seconds that must elapse between one detected collision and the next, to avoid duplicates

        self.ended = False  # whether logging has been finalized and should stop
        # key/value structure that will appear in the final JSON
        self.scenario_data = {
            # metadata
            "tool": self.tool,
            "generation_id": self.generation_id,
            "scenario_id": self.scenario_id,
            "map_name": self.map_name,
            "run_index": self.run_index,
            "start_time": self.start_time,
            "simulation_start_time": self.simulation_start_time,
            "delta_time": self.delta_time,

            "results" : {
                "has_collision": False,
                "has_red_light_violation": False,
                "has_speeding": False,
                "has_stop_violation": False,
                "event_counts": {
                    "collision": 0,
                    "red_light": 0,
                    "speeding": 0,
                    "stop_sign": 0,
                }
            },

            # list of the main events
            "events": {
                # logs any collision of the ego with other dynamic actors + related info
                "collision": [],
                # logs any red-light crossing by the ego + related info
                "red_lights": [],
                # logs any speed-limit violation by the ego + related info
                "speeding": [],
                # logs any stop-sign violation (not observed by the default behavior agent -> extend it if needed)
                "stop_sign": [],
                "lane_invasion": [],
                "off_road": [],
            },

            # ego vehicle mission information (behavior agent)
            "mission": {
                "start_location": None,
                "end_location": None,
                "waypoints": []
            },

            # registry of dynamic actors
            # id : { type, type_id, role, spawn_frame, spawn_transform, (optional despawn_frame) }
            "actors": {},

            # list of frames, each with the ego + other dynamic actors
            "frames": []
        }
        if self.tool != "TMFuzzer" and self.tool != "ScenarioFuzzLLM":
            self._init_from_world()

        self.scenario_data["results"]["event_counts"].setdefault("collision_vehicle", 0)
        self.scenario_data["results"]["event_counts"].setdefault("collision_pedestrian", 0)
        self.scenario_data["results"]["event_counts"].setdefault("collision_static", 0)
        self.scenario_data["results"]["event_counts"].setdefault("lane_invasion", 0)
        self.scenario_data["results"]["event_counts"].setdefault("off_road", 0)


    def _init_from_world(self):
        try:
            if self.world and self.client:
                # Map name
                carla_map_name = self.world.get_map().name
                self.map_name = carla_map_name.split("/")[-1]
                self.scenario_data["map_name"] = self.map_name

                # Delta time
                settings = self.world.get_settings()
                if settings.fixed_delta_seconds and settings.synchronous_mode:
                    self.delta_time = float(settings.fixed_delta_seconds)
                    self.scenario_data["delta_time"] = self.delta_time
                else:
                    print("[CarlaBasicLogger] Delta time was not set correctly by the world.")

                # Binary recording
                if self.record_binary:
                    # correct absolute path
                    self.recorder_path = os.path.abspath(os.path.join(
                        self.output_dir, f"{self.generation_id}_{self.scenario_id}.rec"
                    ))

                    self.client.start_recorder(self.recorder_path, True)
                    print(f"[CarlaBasicLogger] Binary recording started: {self.recorder_path}")
        except Exception as e:
            print(f"[CarlaBasicLogger] Error while initializing from world: {e}")


    def set_mission_from_agent(self, ego_agent: "BehaviorAgent", ego_sp, ego_dp):
        """
        Sets the ego vehicle's mission information on the logger.

        Args:
            ego_agent: the ego vehicle's BehaviorAgent instance
            ego_sp: the ego vehicle's spawn coordinates
            ego_dp: the ego vehicle's destination coordinates
        """
        # collect the spawn and destination locations
        start_location = [ego_sp.location.x, ego_sp.location.y, ego_sp.location.z]
        end_location = [ego_dp.location.x, ego_dp.location.y, ego_dp.location.z]

        # collect the behavior agent's planned route
        planned_route: List[List[float]] = []

        try:
            # get the agent's local planner, which decides the list of waypoints to follow to reach the destination
            local_planner = ego_agent.get_local_planner()

            route = list(local_planner._waypoints_queue)
            for item in route:
                wpt_or_tf = item[0]
                if isinstance(wpt_or_tf, carla.Waypoint):
                    tf = wpt_or_tf.transform
                elif isinstance(wpt_or_tf, carla.Transform):
                    tf = wpt_or_tf
                planned_route.append([tf.location.x, tf.location.y, tf.location.z])
        except Exception:
            planned_route = []

        self.scenario_data["mission"]["start_location"] = list(start_location) if start_location else None
        self.scenario_data["mission"]["end_location"] = list(end_location) if end_location else None
        self.scenario_data["mission"]["waypoints"] = [list(wp) for wp in (planned_route or [])]


    def classify_actor_type(self, actor: carla.Actor) -> str:
        """
        Classifies the actor's type based on its characteristics.

        Args:
            actor: the actor instance to classify

        Returns:
            str: actor type ("vehicle", "pedestrian", "traffic_light", etc.)
        """
        if isinstance(actor, carla.Vehicle):
            return "vehicle"
        elif isinstance(actor, carla.Walker):
            return "pedestrian"
        elif isinstance(actor, carla.TrafficLight):
            return "traffic_light"
        else:
            return "unknown"

    def register_actor(self, actor: carla.Actor, actor_role: str, spawn_transform: List[float], snapshot: carla.WorldSnapshot):
        """
            Registers a dynamic actor.
        """

        if str(actor.id) in self.scenario_data["actors"]:
            # actor already registered
            return False

        if self.classify_actor_type(actor=actor) not in ["vehicle", "pedestrian"]:
            return False

        # build the actor's static information dict
        actor_info = {
            "type_id": actor.type_id,
            "role": actor_role,
            "spawn_frame": self.frame_count,
            "spawn_carla_frame": snapshot.frame,
            "spawn_carla_timestamp": snapshot.timestamp.elapsed_seconds,
            "spawn_transform": spawn_transform
        }

        # add the actor to the actors registry
        self.scenario_data["actors"][actor.id] = actor_info
        return True

    def register_ego_actor(self, ego_vehicle: carla.Vehicle, snapshot: carla.WorldSnapshot):
        """Registers the ego actor on the logger, with its static info (bounding box + spawn transform)."""

        transform = ego_vehicle.get_transform()

        self.register_actor(ego_vehicle,
                            "ego",
                            [transform.location.x, transform.location.y, transform.location.z, transform.rotation.roll, transform.rotation.pitch, transform.rotation.yaw],
                            snapshot=snapshot)

    def update_frame(
            self,
            world: carla.World,
            ego_vehicle: carla.Vehicle,
            snapshot: Optional[carla.WorldSnapshot],
    ):
        """
        Logs one frame, with information on the actors present in the scene.
        Args:
            world: the CARLA world instance
            ego_vehicle: the ego vehicle instance
            snapshot: the CARLA world snapshot instance
        """
        if self.ended:
            return
        ego_tf = ego_vehicle.get_transform()
        ego_velocity = ego_vehicle.get_velocity()
        ego_angular_velocity = ego_vehicle.get_angular_velocity()

        ego_loc = [ego_tf.location.x, ego_tf.location.y, ego_tf.location.z]
        # roll:x, pitch:y, yaw:z
        ego_rot = [ego_tf.rotation.roll, ego_tf.rotation.pitch, ego_tf.rotation.yaw]
        ego_velocity_vec = [ego_velocity.x, ego_velocity.y, ego_velocity.z]
        ego_angular_velocity_vec = [ego_angular_velocity.x, ego_angular_velocity.y, ego_angular_velocity.z]

        # reportedly unreliable from CARLA (compute it a posteriori instead)
        #ego_acc = ego_vehicle.get_acceleration()

        if snapshot is None:
            snapshot = world.get_snapshot()

        carla_frame = snapshot.frame
        current_timestamp = snapshot.timestamp.elapsed_seconds

        if self._first_timestamp is None:
            self._first_timestamp = current_timestamp

        relative_timestamp = current_timestamp - self._first_timestamp
        if self.violation_monitor is None:
            print("ViolationMonitor not set correctly: violations will not be tracked")
        else:
            self.violation_monitor.tick(carla_frame, relative_timestamp)

        frame_data = {
            "frame": self.frame_count,
            "carla_frame": carla_frame,
            "timestamp": relative_timestamp,
            "delta_time": self.delta_time,

            "ego_vehicle": {
                "location": list(ego_loc),
                "rotation": list(ego_rot),
                "velocity": list(ego_velocity_vec),
                "angular_velocity": list(ego_angular_velocity_vec),
                "extra": {}  # to be populated later if needed
            },

            "other_actors": {},
        }

        # for the ego
        ego_bb = ego_vehicle.bounding_box
        ego_vertices: List[carla.Location] = ego_bb.get_world_vertices(ego_vehicle.get_transform())
        ego_vertices_list = [[v.x, v.y, v.z] for v in ego_vertices]

        frame_data["ego_vehicle"]["bounding_box_vertices"] = ego_vertices_list

        for actor_snap in snapshot:
            if not isinstance(actor_snap, carla.ActorSnapshot):
                continue

            actor = world.get_actor(actor_snap.id)
            if actor is None or not actor.is_alive or actor.id == ego_vehicle.id:
                continue

            tf = actor.get_transform()
            loc = actor.get_location()
            vel = actor.get_velocity()
            ang = actor.get_angular_velocity()

            # if not already registered, try to register it
            if str(actor_snap.id) not in self.scenario_data["actors"]:
                registered = self.register_actor(
                    actor=actor,
                    actor_role="npc",
                    spawn_transform=[loc.x, loc.y, loc.z,
                                    tf.rotation.roll, tf.rotation.pitch, tf.rotation.yaw],
                    snapshot=snapshot
                )
                if not registered:
                    continue  # discard non-dynamic actors

            # always update this frame's data for dynamic actors
            frame_data["other_actors"][actor.id] = {
                "type_id": actor.type_id,
                "location": [loc.x, loc.y, loc.z],
                "rotation": [tf.rotation.roll, tf.rotation.pitch, tf.rotation.yaw],
                "velocity": [vel.x, vel.y, vel.z],
                "angular_velocity": [ang.x, ang.y, ang.z],
                "extra": {}
            }

            if not isinstance(actor, (carla.Vehicle, carla.Walker)):
                continue
            # for other dynamic actors
            bb = actor.bounding_box
            vertices = bb.get_world_vertices(actor.get_transform())
            vertices_list = [[v.x, v.y, v.z] for v in vertices]

            frame_data["other_actors"][actor.id]["bounding_box_vertices"] = vertices_list
        self.scenario_data["frames"].append(frame_data)
        self.frame_count += 1

    def finalize_and_save(self, filename: Optional[str] = None) -> str:
        """
            Finalizes the scenario (without computing further metrics) and saves the JSON file.
        """
         # stop the recorder, if active
        if self.record_binary and self.client:
            try:
                self.client.stop_recorder()
                print(f"[CarlaBasicLogger] Binary recording stopped: {self.recorder_path}")

                if self.recorder_path and os.path.exists(self.recorder_path):
                    print(f"[CarlaBasicLogger] Recording file saved: {self.recorder_path}")
                else:
                    print(f"[CarlaBasicLogger] WARNING: file not found: {self.recorder_path}")
            except Exception as e:
                print(f"[CarlaBasicLogger] Error while stopping the recorder: {e}")

        self.scenario_data["results"]["total_frames"] = self.frame_count

        # real-time (wall clock) duration
        sim_end_time = time.time()
        self.scenario_data["results"]["total_simulation_time"] = sim_end_time - self.simulation_start_time

        # simulator-side timestamps
        start_ts = self.scenario_data["frames"][0]["timestamp"]
        end_ts = self.scenario_data["frames"][-1]["timestamp"]
        self.scenario_data["results"]["simulated_time"] = end_ts - start_ts

        if filename is None:
            if self.tool == "ScenarioFuzzLLM":
                filename = f"{self.scenario_id}_run_{self.run_index:02d}_log_basic.json"
            else:
                filename = f"{self.generation_id}_{self.scenario_id}_log_basic.json"
        path = os.path.join(self.output_dir, filename)

        rounded_data = round_floats(self.scenario_data)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(rounded_data, f, indent=2, ensure_ascii=False)
        print(f"[CarlaBasicLogger] Scenario data saved to: {path}")
        self.ended = True
        return path


    def log_collision(self, collision_data):
        """Logs a collision."""
        self.scenario_data["events"]["collision"].append(collision_data)
        self.scenario_data["results"]["has_collision"] = True

        # total counter
        self.scenario_data["results"]["event_counts"]["collision"] += 1

        # category-specific counter
        cat = collision_data.get("collision_category")
        if cat and cat in self.scenario_data["results"]["event_counts"]:
            self.scenario_data["results"]["event_counts"][cat] += 1


    def log_red_light(self, frame: int, timestamp: float, speed_kmh):
        """Logs a red-light crossing."""
        if self.ended:
            return
        self.scenario_data["events"]["red_lights"].append({
            "frame": frame,
            "timestamp": timestamp,
            "speed_kmh": speed_kmh
        })
        print(f"[ViolationMonitor] Red light violation at frame {frame}, timestamp {timestamp}")
        self.scenario_data["results"]["has_red_light_violation"] = True
        self.scenario_data["results"]["event_counts"]["red_light"] += 1

    def log_speeding(self, frame: int, timestamp: float, speed_kmh: float, speed_limit_kmh: float):
        """Logs a (raw) speed-limit violation."""
        if self.ended:
            return
        self.scenario_data["events"]["speeding"].append({
            "frame": int(frame),
            "timestamp": float(timestamp),
            "speed_kmh": float(speed_kmh),
            "speed_limit_kmh": float(speed_limit_kmh)
        })
        print(f"[ViolationMonitor] Speeding violation at frame {frame}, timestamp {timestamp}: ego going {speed_kmh} on a {speed_limit_kmh} limit route")
        self.scenario_data["results"]["has_speeding"] = True
        self.scenario_data["results"]["event_counts"]["speeding"] += 1

    def log_stop_violation(self, frame, timestamp, lm_id, lm_loc, speed_kmh, stopped):
        if self.ended:
            return
        violation = {
            "frame": frame,
            "timestamp": timestamp,
            "landmark_id": lm_id,
            "location": [lm_loc.x, lm_loc.y, lm_loc.z],
            "speed_kmh": speed_kmh,
            "stopped": stopped  # True if it stopped but not long enough, False if it never stopped
        }
        self.scenario_data["events"]["stop_sign"].append(violation)

        status = "TOO SHORT" if stopped else "NOT STOPPED"
        print(f"[ViolationMonitor] Stop sign violation ({status}) at frame {frame}, timestamp {timestamp} (ID={lm_id})")
        self.scenario_data["results"]["has_stop_violation"] = True
        self.scenario_data["results"]["event_counts"]["stop_sign"] += 1

    def log_lane_invasion(self, frame: int, timestamp: float, crossed_markings):
        if self.ended:
            return

        self.scenario_data["events"]["lane_invasion"].append({
            "frame": frame,
            "timestamp": timestamp,
            "crossed_markings": crossed_markings
        })
        self.scenario_data["results"]["event_counts"]["lane_invasion"] += 1

    def log_off_road(self, frame: int, timestamp: float, location):
        if self.ended:
            return

        self.scenario_data["events"]["off_road"].append({
            "frame": frame,
            "timestamp": timestamp,
            "location": [location.x, location.y, location.z],
        })

        self.scenario_data["results"]["event_counts"]["off_road"] += 1


    @staticmethod
    def handle_collision(event: carla.CollisionEvent, state):
        logger = LOGGER_REGISTRY.get(event.actor.id)
        if not logger or logger.ended:
            return

        # --------------------------------------------------
        # 1. Avoid counting multiple collisions with the same actor
        # --------------------------------------------------
        if logger.last_collision_actor_id == event.other_actor.id:
            return

        # --------------------------------------------------
        # 2. Classify the collision type (Leaderboard-style)
        # --------------------------------------------------
        if isinstance(event.other_actor, carla.Vehicle):
            collision_category = "collision_vehicle"
        elif isinstance(event.other_actor, carla.Walker):
            collision_category = "collision_pedestrian"
        else:
            collision_category = "collision_static"

        # --------------------------------------------------
        # 3. Update tool-specific state, if needed
        # --------------------------------------------------
        if logger.tool == "SimADFuzz":
            CarlaBasicLogger.handle_simadfuzz_state(event, state)

        elif logger.tool == "TMFuzzer":
            CarlaBasicLogger.handle_tmfuzz_state(event, state)

        elif logger.tool == "ScenarioFuzzLLM":
            CarlaBasicLogger.handle_scenariofuzzllm_state(event, state)

        # --------------------------------------------------
        # 4. Build the (logger-consistent) collision data
        # --------------------------------------------------
        collision_data = analyze_collision(event=event)

        collision_data["collision_category"] = collision_category
        collision_data["other_actor_id"] = event.other_actor.id
        collision_data["other_actor_type_id"] = event.other_actor.type_id

        # frame and timestamp, consistent with the logger
        collision_data["frame"] = logger.frame_count

        # timestamp relative to the start of the simulation
        if logger._first_timestamp is not None:
            collision_data["timestamp"] = (
                logger.world.get_snapshot().timestamp.elapsed_seconds
                - logger._first_timestamp
            )
        else:
            collision_data["timestamp"] = 0.0

        print(
            f"[HAZARD] Collision ({collision_category}) with "
            f"{event.other_actor.type_id} (ID {event.other_actor.id})"
        )

        # --------------------------------------------------
        # 5. Log the collision
        # --------------------------------------------------
        logger.log_collision(collision_data)

        # --------------------------------------------------
        # 6. Update the last actor involved
        # --------------------------------------------------
        logger.last_collision_actor_id = event.other_actor.id



    @staticmethod
    def handle_simadfuzz_state(event:carla.CollisionEvent,state):
        # early-exit if the state has already flagged an early stop
        if state.early_stop:
            return

        state.crashed = True
        state.early_stop = True
        state.early_stop_reason = "Ego collision"
        state.violation_found = True
        state.collision_details.append((event.timestamp, event.transform))


    @staticmethod
    def handle_scenariofuzzllm_state(event: carla.CollisionEvent, state):
        if state.end:
            # ignore collision happened AFTER simulation ends
            # (can happen because of sluggish garbage collection of Carla)
            return
        if event.other_actor.type_id != "static.road":
            if not state.crashed:
                print("COLLISION:", event.other_actor.type_id)
                # do not count collision while spawning ego vehicle (hard drop)
                state.crashed = True
                state.collision_to = event.other_actor.id
                state.min_dist = 0
                state.min_dist_frame = state.num_frames

    @staticmethod
    def handle_tmfuzz_state(event:carla.CollisionEvent,state):
        if state.end:
            # ignore collision happened AFTER simulation ends
            # (can happen because of sluggish garbage collection of Carla)
            return
        if event.other_actor.type_id != "static.road":
            if not state.crashed:
                print("COLLISION:", event.other_actor.type_id)
                # do not count collision while spawning ego vehicle (hard drop)
                state.crashed = True
                state.collision_to = event.other_actor.id


def round_floats(data: Any, decimals=4) -> Any:
    if isinstance(data, float):
        return round(data, decimals)
    elif isinstance(data, list):
        return [round_floats(x, decimals) for x in data]
    elif isinstance(data, dict):
        return {k: round_floats(v, decimals) for k, v in data.items()}
    else:
        return data
