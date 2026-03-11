"""Dynamic Window Approach trajectory evaluation against a cost map."""

import math
import numpy as np

from backend.services.path_planner.schemas import GridConfig, CandidateTrajectory

# Robot physical parameters
ROBOT_WIDTH_M = 0.17
ROBOT_LENGTH_M = 0.20
MIN_TURN_RADIUS_M = 0.30

# Trajectory sampling
N_STEERING = 15        # steering angles: -1.0 to 1.0 in steps of ~0.14
N_SPEEDS = 3           # speed levels: 0.33, 0.66, 1.0
LOOK_AHEAD_M = 1.5     # forward distance for trajectory arcs
N_SAMPLE_POINTS = 10   # points per trajectory arc

# Maximum arc angle to prevent trajectory wrapping past a half-circle
MAX_ARC_THETA = math.pi * 0.75

# Feasibility thresholds
MAX_COST_THRESHOLD = 0.9   # reject if any point cost exceeds this
MIN_CLEARANCE_M = 0.08     # reject if clearance < 8 cm

# Emergency stop distance
EMERGENCY_STOP_M = 0.25

# Scoring weights
W_OBSTACLE = 0.30
W_HEADING = 0.20
W_SMOOTHNESS = 0.10
W_CLEARANCE = 0.15
W_PROGRESS = 0.25


def _build_candidates() -> list[tuple[float, float]]:
    """Generate (steering, speed) pairs for all candidate trajectories."""
    steerings = np.linspace(-1.0, 1.0, N_STEERING)
    speeds = np.linspace(1.0 / N_SPEEDS, 1.0, N_SPEEDS)
    candidates = []
    for s in steerings:
        for v in speeds:
            candidates.append((float(s), float(v)))
    return candidates


# Precompute candidate parameter pairs
_CANDIDATES = _build_candidates()


class TrajectoryEvaluator:
    def __init__(self, config: GridConfig, emergency_stop_m: float = EMERGENCY_STOP_M):
        self._cfg = config
        self._emergency_stop_m = emergency_stop_m
        self._prev_steering = 0.0
        self._prev_speed = 0.0

    def _arc_points(self, steering: float, speed: float) -> list[tuple[float, float]]:
        """Sample world-coordinate points along a circular arc.

        steering: [-1, 1] maps to turning.  0 = straight.
        Returns list of (x_m, y_m) in robot-centric world frame.
        Robot is at (0, 0), facing +y.
        """
        points: list[tuple[float, float]] = []
        arc_length = LOOK_AHEAD_M * speed  # shorter arc at lower speed

        if abs(steering) < 0.05:
            # Straight line
            for i in range(1, N_SAMPLE_POINTS + 1):
                y = arc_length * i / N_SAMPLE_POINTS
                points.append((0.0, y))
        else:
            # Circular arc
            # Map steering to turn radius (|steering|=1 → min radius)
            radius = MIN_TURN_RADIUS_M / abs(steering)
            # Total angle subtended — cap to prevent wrapping
            theta_total = min(arc_length / radius, MAX_ARC_THETA)
            sign = 1.0 if steering > 0 else -1.0  # positive steering = turn right

            for i in range(1, N_SAMPLE_POINTS + 1):
                theta = theta_total * i / N_SAMPLE_POINTS
                # Centre of turning circle is at (sign * radius, 0)
                x = sign * radius * (1 - math.cos(theta))
                y = radius * math.sin(theta)
                points.append((x, y))

        return points

    def _world_to_grid(self, x_m: float, y_m: float) -> tuple[int, int]:
        col = int((x_m + self._cfg.width_m / 2) / self._cfg.cell_size_m)
        row = int((self._cfg.depth_m - y_m) / self._cfg.cell_size_m)
        col = max(0, min(col, self._cfg.cols - 1))
        row = max(0, min(row, self._cfg.rows - 1))
        return (row, col)

    def evaluate(
        self,
        cost_map: np.ndarray,
        nearest_obstacle_m: float,
        occupancy: np.ndarray | None = None,
    ) -> list[CandidateTrajectory]:
        """Score all candidate trajectories against the cost map.

        Parameters
        ----------
        cost_map : full weighted cost map (used for obstacle scoring)
        nearest_obstacle_m : closest obstacle distance
        occupancy : raw occupancy grid (obstacle-only), used for clearance
                    estimation.  Falls back to cost_map if not provided.

        Returns sorted list (best first).
        """
        clearance_grid = occupancy if occupancy is not None else cost_map
        results: list[CandidateTrajectory] = []

        for steering, speed in _CANDIDATES:
            ct = CandidateTrajectory(steering=steering, speed=speed)

            # Emergency stop override
            if nearest_obstacle_m < self._emergency_stop_m:
                ct.feasible = False
                ct.total_cost = 999.0
                results.append(ct)
                continue

            # Sample arc points
            pts = self._arc_points(steering, speed)

            # Evaluate along the trajectory
            costs_along: list[float] = []
            min_clearance = 999.0
            infeasible = False

            for x_m, y_m in pts:
                r, c = self._world_to_grid(x_m, y_m)
                cell_cost = float(cost_map[r, c])
                costs_along.append(cell_cost)

                if cell_cost > MAX_COST_THRESHOLD:
                    infeasible = True
                    break

                # Clearance from occupancy grid (obstacle-only, not static costs)
                occ_val = float(clearance_grid[r, c])
                clearance_est = (1.0 - occ_val) * self._cfg.width_m / 2
                min_clearance = min(min_clearance, clearance_est)

            if infeasible or min_clearance < MIN_CLEARANCE_M:
                ct.feasible = False
                ct.total_cost = 999.0
                results.append(ct)
                continue

            # --- Scoring components ---

            # Obstacle cost: average cost along trajectory
            ct.obstacle_cost = float(np.mean(costs_along)) if costs_along else 1.0

            # Heading cost: prefer straight ahead (steering = 0)
            ct.heading_cost = abs(steering)

            # Smoothness: penalise large change from previous command
            ct.smoothness_cost = abs(steering - self._prev_steering) / 2.0

            # Clearance: prefer trajectories far from obstacles
            ct.clearance_cost = 1.0 - min(min_clearance / (self._cfg.width_m / 2), 1.0)

            # Progress: prefer higher speed + forward movement
            # Use max forward y across all points to handle arcs correctly
            max_y = max(p[1] for p in pts) if pts else 0.0
            ct.progress_cost = 1.0 - max(0.0, min(max_y / LOOK_AHEAD_M, 1.0))

            # Weighted total
            ct.total_cost = (
                W_OBSTACLE * ct.obstacle_cost
                + W_HEADING * ct.heading_cost
                + W_SMOOTHNESS * ct.smoothness_cost
                + W_CLEARANCE * ct.clearance_cost
                + W_PROGRESS * ct.progress_cost
            )

            results.append(ct)

        # Sort by total cost (ascending — lower is better)
        results.sort(key=lambda t: t.total_cost)

        return results

    def set_previous(self, steering: float, speed: float):
        """Update previous command for smoothness scoring."""
        self._prev_steering = steering
        self._prev_speed = speed
