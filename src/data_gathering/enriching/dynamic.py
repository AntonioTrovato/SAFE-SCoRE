from typing import List, Dict, Any, Tuple

import numpy as np


class DynamicsAnalyzer:
    """
    Analyzes the longitudinal dynamics of the ego vehicle
    by computing simple statistical indicators on speed and acceleration.
    """

    def __init__(self, frames: List[Dict[str, Any]], delta_time: float = 0.05):
        self.frames = frames
        self.dt = delta_time

    def analyze(self) -> Dict[str, float]:
        timestamps, v_long, v_lat = self.extract_signals()
        if len(timestamps) < 2:
            return self.empty_result()

        # total speed (hypotenuse of v_long, v_lat)
        speed = np.hypot(v_long, v_lat)
        mean_speed = float(np.mean(speed))
        max_speed = float(np.max(speed))

        # longitudinal acceleration (numerical derivative)
        acc_long = np.diff(v_long) / self.dt if v_long.size > 1 else np.array([])
        abs_acc = np.abs(acc_long)

        mean_acc = float(np.mean(abs_acc)) if abs_acc.size > 0 else 0.0
        p95_acc = float(np.percentile(abs_acc, 95)) if abs_acc.size > 0 else 0.0
        max_acc = float(np.max(abs_acc)) if abs_acc.size > 0 else 0.0

        return {
            "mean_speed": mean_speed,
            "max_speed": max_speed,
            "mean_long_acc": mean_acc,
            "p95_long_acc": p95_acc,
            "max_long_acc": max_acc,
        }

    def extract_signals(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Extracts timestamps and longitudinal/lateral speeds from the ego vehicle."""
        timestamps, v_long, v_lat = [], [], []
        for frame in self.frames:
            ego = frame.get("ego_vehicle")
            if not ego or "velocity" not in ego or "rotation" not in ego:
                continue

            vx, vy = float(ego["velocity"][0]), float(ego["velocity"][1])
            yaw_rad = np.radians(float(ego["rotation"][2]))

            # transform into vehicle coordinates
            forward_x, forward_y = np.cos(yaw_rad), np.sin(yaw_rad)
            right_x, right_y = -np.sin(yaw_rad), np.cos(yaw_rad)

            v_longitudinal = vx * forward_x + vy * forward_y
            v_lateral = vx * right_x + vy * right_y

            v_long.append(v_longitudinal)
            v_lat.append(v_lateral)
            timestamps.append(float(frame.get("timestamp", 0.0)))

        return np.array(timestamps), np.array(v_long), np.array(v_lat)

    def empty_result(self) -> Dict[str, float]:
        return {
            "mean_speed": 0.0,
            "max_speed": 0.0,
            "mean_long_acc": 0.0,
            "p95_long_acc": 0.0,
            "max_long_acc": 0.0,
        }