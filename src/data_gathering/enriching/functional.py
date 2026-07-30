from dataclasses import dataclass
from typing import Dict, List, Tuple, Any, Optional

import numpy as np


@dataclass(frozen=True)
class DeviationStats:
    """Holds the statistics on the lateral deviations from the route."""
    mean: float
    rmse: float
    mae: float
    max_deviation: float
    std_dev: float

    def to_dict(self) -> Dict[str, float]:
        return self.__dict__

@dataclass(frozen=True)
class EgoJourney:
    """Represents the actual path travelled by the ego vehicle"""
    positions: np.ndarray
    timestamps: np.ndarray
    frame_ids: np.ndarray

@dataclass(frozen=True)
class FunctionalMetricResult:
    """Final, aggregated result of the functional metrics."""
    completion_rate: float
    route_following_stability: float
    time_to_completion: Optional[float]
    total_planned_distance: float
    actual_distance_traveled: float
    max_progress_reached: float
    deviation_stats: DeviationStats
    completion_frame: Optional[int]
    completion_timestamp: Optional[float]
    dist_to_goal_final: Optional[float] = None
    is_completed_final: Optional[bool] = None
    completion_method: Optional[str] = None
    waypoint_alignment_ok: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "completion_rate": self.completion_rate,
            "route_following_stability": self.route_following_stability,
            "time_to_completion": self.time_to_completion,
            "total_planned_distance": self.total_planned_distance,
            "actual_distance_traveled": self.actual_distance_traveled,
            "max_progress_reached": self.max_progress_reached,
            "deviation_stats": self.deviation_stats.to_dict(),
            "completion_frame": self.completion_frame,
            "completion_timestamp": self.completion_timestamp,
            "dist_to_goal_final": self.dist_to_goal_final,
            "is_completed_final": self.is_completed_final,
            "completion_method": self.completion_method,
            "waypoint_alignment_ok": self.waypoint_alignment_ok,
        }


class Route:
    """
    Represents a planned route as a 2D polyline.
    It is responsible for all the geometric logic related to the route itself.
    """
    def __init__(self, waypoints: List[List[float]]):
        if len(waypoints) < 2:
            raise ValueError("A valid route requires at least 2 waypoints")

        #keep only x, y coordinates
        waypoints_2d = np.array(waypoints)[:, :2]

        #vectors representing the "difference" between consecutive waypoints
        segment_vectors = np.diff(waypoints_2d, axis=0)

        #compute the magnitude of these vectors
        segment_lengths = np.linalg.norm(segment_vectors, axis=1)

        valid_segment_mask = segment_lengths > 1e-9
        if not np.any(valid_segment_mask):
            raise ValueError("The route does not contain any segment of valid length")

        # prepend true so the first waypoint is always included.
        # this aligns the mask with the waypoints (not with the segments).
        waypoints_mask = np.insert(valid_segment_mask, 0, True)

        valid_indices = np.where(waypoints_mask)[0]
        self.waypoints = waypoints_2d[valid_indices]
        self.segment_lengths = segment_lengths[valid_segment_mask]

        # Cumulative sum of the segment lengths
        segment_cumsum = np.cumsum(self.segment_lengths)

        # Insert 0 at the beginning to indicate the initial distance is zero
        self.cumulative_distances = np.insert(segment_cumsum, 0, 0.0)

        # The total route length is the last cumulative distance
        self.total_length = self.cumulative_distances[-1]

    def project(self, point: np.ndarray):
        """
        Projects a 2D point onto the route to find
        the point on the route's line that is closest to the given point.

        Returns:
            - s (float): Curvilinear progress (distance from the route start to the projected point).
            - d (float): Lateral deviation (distance between the point and its projection).
        """
        p = point[:2]
        min_lateral_dist = float("inf") # smallest lateral deviation found so far
        best_progress = 0.0  # corresponding curvilinear progress

        for i in range(len(self.segment_lengths)):
            a = self.waypoints[i]   #segment start point
            b = self.waypoints[i+1] #segment end point

            #project the point p onto the segment [a, b]
            t, lateral_dist = self.project_on_segment(p, a, b)

            # if this projection is closer to the route, keep it
            if lateral_dist < min_lateral_dist:
                min_lateral_dist = lateral_dist

                # compute the progress along this segment
                progress_on_segment = t * self.segment_lengths[i]

                # total progress = cumulative distance + progress within the segment
                best_progress = self.cumulative_distances[i] + progress_on_segment

        return best_progress, min_lateral_dist

    @staticmethod
    def project_on_segment(p: np.ndarray, a: np.ndarray, b: np.ndarray):
        """
        Projects a point 'p' onto a single segment 'ab'.
        """
        # vector from point 'a' to point 'b'
        ab = b - a
        # vector from point 'a' to point 'p'
        ap = p - a

        ab_len_sq = np.dot(ab, ab) # squared length of segment 'ab'

        #if the segment length is essentially 0, treat it as a point
        if ab_len_sq < 1e-9:
            return 0.0, np.linalg.norm(ap)

        #dot product between ab and ap
        dot_product = np.dot(ap, ab)

        # Dividing by the squared length of 'ab' normalizes this value
        # to get 't', a parameter telling us where the projection falls.
        # - If t = 0, the projection is at 'a'.
        # - If t = 1, the projection is at 'b'.
        # - If 0 < t < 1, the projection is between 'a' and 'b'.
        # - If t < 0 or t > 1, the projection falls outside segment 'ab'.
        # since we want the projection on segment ab, clamp t to the range [0, 1]
        t = np.clip((dot_product / ab_len_sq), 0.0, 1.0)

        # compute the coordinates of the projected point
        # starting from 'a' and moving along the direction of segment 'ab'
        # by a factor 't'
        projection_point = a + ab * t

        # the lateral distance equals the euclidean distance between
        # the original point 'p' and its shadow 'projection_point'
        distance = np.linalg.norm(p - projection_point)

        return t, distance

def compute_deviation_stats(deviations: np.ndarray) -> DeviationStats:
    """Computes basic statistics from an array of lateral deviations."""
    if deviations.size == 0:
        return DeviationStats(0, 0, 0, 0, 0)
    return DeviationStats(
        mean=float(np.mean(deviations)),
        rmse=float(np.sqrt(np.mean(deviations**2))),
        mae=float(np.mean(np.abs(deviations))),
        max_deviation=float(np.max(deviations)),
        std_dev=float(np.std(deviations))
    )

def compute_route_following_stability(mean_devation:float, threshold: float) -> float:
    """Computes a stability score (0-100) based on the mean deviation."""
    # If the deviation is zero: perfect stability
    if mean_devation <= 0:
        return 100.0

    # Compute a negative coefficient proportional to the deviation
    # as the deviation increases, the coefficient (decay) becomes even smaller
    decay = -mean_devation / threshold

    # Convert the deviation into a score via exponential decay
    score = 100 * np.exp(decay)

    return float(np.clip(score, 0.0, 100.0))

def compute_traveled_distance(positions: np.ndarray) -> float:
    """Computes the total distance travelled by the vehicle."""
    if len(positions) < 2:
        return 0.0

    # Differences between consecutive positions (x,y coordinates only)
    # this gives the displacement vectors between each pair of points
    displacements = np.diff(positions[:, :2], axis=0)

    # Compute the length (norm) of each displacement
    # these are the distances covered in each individual segment
    segment_lengths = np.linalg.norm(displacements, axis=1)

    # Sum all the distances to obtain the total path length
    return float(np.sum(segment_lengths))

def find_completion_time(journey: EgoJourney, progress: np.ndarray, total_dist: float, tolerance: float) -> Tuple:
    """Identifies the first instant at which the mission is considered complete."""
    goal_dist = total_dist - tolerance
    completed_indices = np.where(progress >= goal_dist)[0]

    if completed_indices.size > 0:
        idx = completed_indices[0]
        return int(journey.frame_ids[idx]), float(journey.timestamps[idx]), journey.timestamps[idx] - journey.timestamps[0]

    return None, None, None


def _euclidean_2d(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a[:2] - b[:2]))


def _find_completion_by_goal_distance(
    journey: EgoJourney,
    goal_xy: np.ndarray,
    tolerance: float,
) -> Tuple[Optional[int], Optional[float], Optional[float]]:
    """Robust fallback: considered complete if the 2D distance to the goal <= tolerance."""
    if journey.positions.shape[0] == 0:
        return None, None, None
    dists = np.linalg.norm(journey.positions[:, :2] - goal_xy[:2], axis=1)
    idxs = np.where(dists <= tolerance)[0]
    if idxs.size == 0:
        return None, None, None
    idx = int(idxs[0])
    return int(journey.frame_ids[idx]), float(journey.timestamps[idx]), float(journey.timestamps[idx] - journey.timestamps[0])


class FunctionalAnalyzer:
    """
    Orchestrates the computation of the performance metrics.
    """
    def __init__(self, output_dir, completion_tolerance: float = 10.0, stability_threshold: float = 5.0):
        self.completion_tolerance = completion_tolerance
        self.stability_threshold = stability_threshold
        self.output_dir = output_dir

    def analyze(self, log_data: Dict[str, Any]) -> FunctionalMetricResult:
        """Runs the full log analysis and returns the metrics."""

        mission = log_data.get("mission", {}) or {}
        waypoints = mission.get("waypoints", []) or []
        start_loc = mission.get("start_location")
        end_loc = mission.get("end_location")

        # Extract the ego's journey
        journey = self.extract_journey_from_log(log_data.get("frames", []))

        # Final distance to the goal (robust, does not depend on waypoints)
        dist_to_goal_final = None
        if end_loc and len(end_loc) >= 2 and journey.positions.shape[0] > 0:
            goal_xy = np.array(end_loc[:2], dtype=float)
            dist_to_goal_final = float(np.linalg.norm(journey.positions[-1, :2] - goal_xy))

        # Sanity check: are the waypoints aligned?
        waypoint_alignment_ok = True
        if len(waypoints) < 2:
            waypoint_alignment_ok = False
        elif journey.positions.shape[0] == 0:
            waypoint_alignment_ok = False
        else:
            wp0 = np.array(waypoints[0][:2], dtype=float)
            if start_loc and len(start_loc) >= 2:
                st = np.array(start_loc[:2], dtype=float)
            else:
                st = journey.positions[0, :2]
            # If the first waypoint is far from the start, the route is probably in a different reference frame
            if float(np.linalg.norm(wp0 - st)) > 50.0:
                waypoint_alignment_ok = False

        # Default output
        route_total = 0.0
        max_progress = 0.0
        completion_rate = 0.0
        deviations = np.array([])
        completion_frame = None
        completion_ts = None
        ttc = None
        completion_method = None

        # Method 1: projection onto the route (only if aligned)
        if waypoint_alignment_ok:
            try:
                route = Route(waypoints)
                route_total = float(route.total_length)

                if journey.positions.shape[0] == 0:
                    projections = np.empty((0, 2))
                else:
                    projections = np.array([route.project(pos) for pos in journey.positions])

                progress = projections[:, 0] if projections.size > 0 else np.array([])
                deviations = projections[:, 1] if projections.size > 0 else np.array([])

                max_progress = float(np.max(progress)) if progress.size > 0 else 0.0
                completion_rate = (max_progress / route.total_length) * 100 if route.total_length > 0 else 0.0

                completion_frame, completion_ts, ttc = find_completion_time(
                    journey, progress, route.total_length, self.completion_tolerance
                )

                completion_method = "route_projection"
            except Exception:
                # fallback below
                waypoint_alignment_ok = False

        # Method 2: robust fallback based on the final distance to the goal
        if not waypoint_alignment_ok and end_loc and len(end_loc) >= 2:
            goal_xy = np.array(end_loc[:2], dtype=float)
            completion_frame, completion_ts, ttc = _find_completion_by_goal_distance(
                journey, goal_xy, self.completion_tolerance
            )
            completion_rate = 100.0 if completion_frame is not None else 0.0
            completion_method = "goal_distance"

        deviation_stats = compute_deviation_stats(deviations)
        stability_score = compute_route_following_stability(deviation_stats.mean, self.stability_threshold)
        actual_distance = compute_traveled_distance(journey.positions)

        is_completed_final = True if completion_frame is not None else False

        return FunctionalMetricResult(
            completion_rate=completion_rate,
            route_following_stability=stability_score,
            time_to_completion=ttc,
            total_planned_distance=route_total,
            actual_distance_traveled=actual_distance,
            max_progress_reached=max_progress,
            deviation_stats=deviation_stats,
            completion_frame=completion_frame,
            completion_timestamp=completion_ts,
            dist_to_goal_final=dist_to_goal_final,
            is_completed_final=is_completed_final,
            completion_method=completion_method,
            waypoint_alignment_ok=waypoint_alignment_ok,
        )


    def extract_journey_from_log(self, frames: List[Dict]) -> EgoJourney:
        """Extracts and cleans the ego's path data from the logs."""
        positions, timestamps, frame_ids = [], [], []

        for f in frames:
            loc = f.get("ego_vehicle", {}).get("location")
            if loc:
                positions.append(loc[:3])
                timestamps.append(f.get("timestamp", 0.0))
                frame_ids.append(f.get("frame", len(frame_ids)))

        return EgoJourney(np.array(positions), np.array(timestamps), np.array(frame_ids))

    def analyze_to_dict(self, log_data: Dict[str, Any]) -> Dict[str, Any]:
        """Convenience method to get the result directly as a dictionary."""
        result = self.analyze(log_data)
        return {"performance": result.to_dict()}
