import logging
import math
from dataclasses import dataclass, field
from typing import Any, List, Dict, Optional

from shapely.geometry import Polygon


def polygon_from_vertices(vertices:List) -> Optional[Polygon]:
    """
    Converts a list of 3D vertices into a valid 2D polygon
    to compute the distance between actors more precisely.
    """
    #convert from 3d to 2d to simplify the calculations
    points_2d = [(float(vertex[0]), float(vertex[1])) for vertex in vertices]

    #remove consecutive duplicate vertices
    unic_points = []
    for index, point in enumerate(points_2d):
        if index == 0 or point != points_2d[index -1]:
            unic_points.append(point)

    if len(unic_points) < 3:
        return None

    #build the initial polygon
    polygon = Polygon(unic_points)

    #if the polygon is malformed
    if not polygon.is_valid:
        #try to fix it by removing anomalies
        polygon = polygon.buffer(0)

    #convert the polygon to its convex hull
    convex_hull = polygon.convex_hull

    return convex_hull if isinstance(convex_hull, Polygon) else None

def relative_speed_magnitude(ego_velocity: List, other_velocity: List) -> float:
    """
    Computes the magnitude of the relative speed between two actors (in 2D)

    Relative velocity = ego_velocity - other_actor_velocity
    """
    #compute relative velocity components in the XY plane
    delta_vx = float(ego_velocity[0] - other_velocity[0])
    delta_vy = float(ego_velocity[1] - other_velocity[1])

    #magnitude of the relative velocity vector
    return math.hypot(delta_vx, delta_vy)

def safe_polygon_distance(polygon_a: Polygon, polygon_b: Polygon):
    """
    Computes the minimum 2D distance between two polygons.

    Calculation logic:
    1. If the polygons intersect: distance = 0 (critical situation)
    2. If separated: minimum euclidean distance between the closest edges
    """
    return 0.0 if polygon_a.intersects(polygon_b) else polygon_a.distance(polygon_b)

@dataclass
class CriticalAnalyzer:
    """
    Aggregator for computing criticality metrics.

    This system keeps cumulative state during the frame-by-frame analysis of a scenario,
    simultaneously tracking:

    1. Minimum distance:
       - Global minimum distance across the whole scenario
       - Event details: frame, involved actor
       - Per-actor tracking: individual MDBV for each dynamic actor

    2. min_TTC (minimum Time To Collision):
       - Global minimum TTC recorded
       - Metadata: time instant, responsible actor

    3. TET (Time-Exposed Time-to-Collision):
       - Total time spent below the critical TTC threshold
       - Longest continuous streak (worst-case exposure)
       - Tracking of the risk time windows
    """

    # ==========
    # Configuration parameters
    # ==========
    ttc_threshold: float = 1.5 #Critical TTC threshold below which TET is computed
    delta_time: float = 0.05   #Time duration of each frame

    # ==========
    # MDBV state
    # ==========
    min_distance: float = float("inf") #minimum distance from any actor
    min_distance_frame: int = -1       #index of the frame where the minimum distance was recorded
    min_distance_actor: Dict[str, Any] = field(default_factory=dict) #actor details: {id, type_id}

    #per-actor distance tracking - structure: {actor_id : {type_id, min_distance, frame}}
    min_distance_per_actor: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # ==========
    # TTC state
    # ==========
    min_ttc: float = float("inf") #minimum ttc
    min_ttc_frame: int = -1       #index of the frame where the minimum ttc was recorded
    min_ttc_actor: Dict[str, Any] = field(default_factory=dict) #actor responsible for the minimum ttc

    # ==========
    # TET state
    # ==========

    #total time accumulator: sum of all periods below ttc_threshold
    total_exposure: float = 0.0

    #continuous streak management
    current_streak: float = 0.0 #duration of the current streak below threshold
    max_streak: float = 0.0     #longest streak duration ever recorded

    #longest streak details
    max_streak_start_frame: Optional[int] = None
    max_streak_end_frame: Optional[int] = None

    #current streak tracking
    current_streak_start_frame: Optional[int] = None

    def update(self, frame_index: int, frame_min_tcc: float, frame_metrics: List[Dict]):
        """
        Updates the aggregated state with the data from a newly analyzed frame.

        Operations performed:
        1. Update global and per-actor MDBV
        2. Update global min_TTC and identify the responsible actor
        3. Advance TET

        TET logic:
            - TTC <= threshold: extends the current streak or starts a new one
            - TTC > threshold: closes the current streak and updates the statistics
        """

        # ==========
        # MDBV update
        # ==========

        for metric in frame_metrics:
            actor_id = metric["actor_id"]
            current_distance = metric["distance"]
            actor_type = metric["actor_type"]

            #check if this is the new minimum distance
            if current_distance < self.min_distance:
                self.min_distance = current_distance
                self.min_distance_frame = frame_index
                self.min_distance_actor = {
                    "id": actor_id,
                    "type_id": actor_type
                }

            #update MDBV for this specific actor
            if (actor_id not in self.min_distance_per_actor or
                current_distance < self.min_distance_per_actor[actor_id]["min_distance"]):

                self.min_distance_per_actor[actor_id] = {
                    "type_id": actor_type,
                    "min_distance": current_distance,
                    "frame": frame_index
                }

        # ==========
        # TTC update
        # ==========

        if frame_min_tcc < self.min_ttc:
            self.min_ttc = frame_min_tcc
            self.min_ttc_frame = frame_index

            self.min_ttc_actor = {}
            for metric in frame_metrics:
                if metric.get("ttc") == frame_min_tcc:
                    self.min_ttc_actor = {
                        "id": metric["actor_id"],
                        "type_id": metric["actor_type"]
                    }

        # ==========
        # TET update
        # ==========

        if frame_min_tcc <= self.ttc_threshold:
            #start a new exposure streak
            if self.current_streak == 0.0:
                self.current_streak_start_frame = frame_index

            #extend the current streak
            self.current_streak += self.delta_time
        else:
            #close any streak in progress
            self.end_streak(frame_index)

    def end_streak(self, current_frame_index: int):
        """
        Ends a continuous exposure streak and updates the TET statistics.

        This method is called when:
        1. The TTC exceeds the critical threshold (end of risky situation)
        2. The end of the scenario is reached (finalization)
        """

        #check whether there is an active streak to close
        if self.current_streak > 0:
            #accumulate into the total exposure time
            self.total_exposure += self.current_streak

            #check whether this streak is the longest recorded so far
            if self.current_streak > self.max_streak:
                self.max_streak = self.current_streak
                self.max_streak_start_frame = self.current_streak_start_frame
                #the streak ends at the frame right before the current one
                self.max_streak_end_frame = current_frame_index - 1

            #reset for the next streak
            self.current_streak = 0.0
            self.current_streak_start_frame = None

    def get_results(self) -> Dict[str, Any]:
        """
        Builds the final, complete result of the criticality analysis
        by assembling all computed metrics into a dictionary.
        """

        # Convert the dictionary into a list ordered by increasing MDBV
        actors_ordered_by_distance = []
        sorted_actors = []
        if self.min_distance_per_actor:
            # Sort by increasing minimum distance
            sorted_actors = sorted(
                self.min_distance_per_actor.items(),
                key=lambda element: element[1]["min_distance"]
            )

        for actor_id, data in sorted_actors:
            actors_ordered_by_distance.append({
                "actor_id": actor_id,
                "type_id": data["type_id"],
                "min_distance": data["min_distance"],
                "frame": data["frame"]
            })

        return {
            # MDBV metrics
            "MDBV": self.min_distance if self.min_distance != float("inf") else None,
            "MDBV_frame": self.min_distance_frame,
            "MDBV_actor": self.min_distance_actor,

            # min_TTC metrics (minimum Time To Collision)
            "min_TTC": self.min_ttc if self.min_ttc != float("inf") else None,
            "min_TTC_frame": self.min_ttc_frame,
            "min_TTC_actor": self.min_ttc_actor,

            # TET metrics (Total Exposure Time)
            "TET_total": round(self.total_exposure, 3),
            "TET_max": round(self.max_streak, 3),
            "TET_max_start_frame": self.max_streak_start_frame,
            "TET_max_end_frame": self.max_streak_end_frame,

            # Detailed per-actor analysis
            "MDBV_per_actor": actors_ordered_by_distance,
        }

def calculate_scenario_metrics(frames: List[Dict], ttc_threshold: float = 1.5, delta_time: float = 0.05):
    """
    Orchestrator function - analyzes a sequence of frames
    to compute the criticality metrics.
    """

    if not frames:
        #empty input
        return {}

    aggregator = CriticalAnalyzer(ttc_threshold=ttc_threshold, delta_time=delta_time)
    processed_frames = 0

    for frame_index, frame in enumerate(frames):
        #1. Extract and validate the ego-vehicle geometry
        ego_data = frame["ego_vehicle"]
        ego_polygon = polygon_from_vertices(ego_data["bounding_box_vertices"])

        if ego_polygon is None:
            logging.warning(
                f"Frame {frame_index}: could not build the polygon for the ego vehicle. "
                f"Skipping this frame."
            )
            continue

        ego_velocity = ego_data.get("velocity", [0, 0, 0])

        #2. Prepare data structures for the current frame
        frame_metrics: List[Dict] = []
        min_ttc_frame = float("inf")

        other_actors = frame.get("other_actors", {})

        if not other_actors:
            continue

        processed_frames += 1

        #3. Analyze the ego-actor pair for each other dynamic actor
        for actor_id, actor_data in other_actors.items():
            actor_polygon = polygon_from_vertices(actor_data["bounding_box_vertices"])
            if actor_polygon is None:
                continue

            distance = safe_polygon_distance(ego_polygon, actor_polygon)

            actor_velocity = actor_data.get("velocity", [0, 0, 0])
            relative_speed = relative_speed_magnitude(ego_velocity, actor_velocity)

            if relative_speed > 1e-6:
                ttc = distance / relative_speed
            else:
                ttc = float("inf")

            min_ttc_frame = min(min_ttc_frame, ttc)

            actor_metric = {
                "actor_id": actor_id,
                "actor_type": actor_data["type_id"],
                "distance": distance,
                "ttc": ttc
            }
            frame_metrics.append(actor_metric)

        aggregator.update(frame_index, min_ttc_frame, frame_metrics)

    aggregator.end_streak(processed_frames)
    return aggregator.get_results()

