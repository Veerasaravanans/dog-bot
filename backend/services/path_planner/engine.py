"""Main path planner orchestrator — runs the full 5-stage pipeline."""

import logging
import math
import time

import numpy as np

from backend.models.schemas import TrackedObstacle, Detection, MotorDirection
from backend.services.path_planner.schemas import GridConfig, PlannerOutput
from backend.services.path_planner.occupancy_grid import OccupancyGridBuilder
from backend.services.path_planner.obstacle_estimator import ObstacleStateEstimator
from backend.services.path_planner.cost_map import CostMapGenerator
from backend.services.path_planner.trajectory_evaluator import TrajectoryEvaluator

# Camera constants for world→pixel back-projection
_FRAME_W = 640
_FRAME_H = 480
_VANISH_Y_FRAC = 0.30
_DIST_SCALE = 80.0

logger = logging.getLogger("dogbot.planner")

# Temporal smoothing
EMA_WINDOW = 5          # frames for exponential moving average
EMA_ALPHA = 2.0 / (EMA_WINDOW + 1)

# Direction hold — prevent oscillation (time-based, works at any frame rate)
DIRECTION_HOLD_SECONDS = 0.3


class PathPlannerEngine:
    def __init__(
        self,
        grid_width_m: float = 3.0,
        grid_depth_m: float = 4.0,
        cell_size_m: float = 0.05,
        emergency_stop_dist_m: float = 0.25,
        estop_lateral_m: float = 0.25,
        estop_distance_m: float = 0.35,
        blocked_threshold: int = 5,
        recovery_speed: float = 0.5,
        recovery_steering: float = 0.8,
        steering_bias: float = 0.0,
    ):
        cfg = GridConfig(
            width_m=grid_width_m,
            depth_m=grid_depth_m,
            cell_size_m=cell_size_m,
        )
        self._cfg = cfg
        self._emergency_dist = emergency_stop_dist_m
        self._estop_lateral_m = estop_lateral_m
        self._steering_bias = steering_bias

        self._grid_builder = OccupancyGridBuilder(cfg)
        self._estimator = ObstacleStateEstimator()
        self._cost_gen = CostMapGenerator(cfg)
        self._traj_eval = TrajectoryEvaluator(cfg, emergency_stop_m=estop_distance_m)

        # Smoothing state
        self._ema_steering = 0.0
        self._ema_speed = 0.0

        # Direction hold state (time-based)
        self._held_direction: MotorDirection | None = None
        self._hold_until: float = 0.0  # time.monotonic() deadline

        # Latest output (for telemetry)
        self._latest_output = PlannerOutput()

        # Blocked recovery state
        self._consecutive_blocked = 0
        self._recovery_direction = 0.0  # steering for recovery turn
        self._BLOCKED_THRESHOLD = blocked_threshold
        self._RECOVERY_SPEED = recovery_speed
        self._RECOVERY_STEERING = recovery_steering

        # Generation counter — incremented each plan() call so consumers
        # can detect when a genuinely new output is available.
        self._generation: int = 0

    @property
    def latest_output(self) -> PlannerOutput:
        return self._latest_output

    @property
    def generation(self) -> int:
        return self._generation

    def plan(
        self,
        obstacles: list[TrackedObstacle],
        detections: list[Detection],
        edge_map: np.ndarray | None = None,
        floor_obstacle_mask: np.ndarray | None = None,
    ) -> PlannerOutput:
        """Run the full planning pipeline and return continuous steering/speed."""
        t0 = time.perf_counter()
        self._generation += 1

        # 1. Build occupancy grid (includes edge + floor layers when available)
        occupancy = self._grid_builder.update(
            obstacles, detections, edge_map, floor_obstacle_mask
        )

        # 2. Update Kalman filters
        obs_states = self._estimator.update(obstacles, detections)

        # Find nearest obstacle IN THE FORWARD CORRIDOR only.
        # Obstacles to the side should not trigger emergency stop for all trajectories —
        # the cost map and point-based arc check handle those.
        nearest_m = 99.0
        for obs in obs_states:
            if abs(obs.x) < self._estop_lateral_m:
                nearest_m = min(nearest_m, obs.y)

        # 3. Generate cost map
        cost_map = self._cost_gen.generate(occupancy, obs_states, self._estimator)

        # 4. Evaluate trajectories (pass raw occupancy for clearance estimation)
        candidates = self._traj_eval.evaluate(cost_map, nearest_m, occupancy=occupancy)

        feasible = [c for c in candidates if c.feasible]
        feasible_count = len(feasible)

        if not feasible:
            self._consecutive_blocked += 1

            if self._consecutive_blocked >= self._BLOCKED_THRESHOLD:
                # Recovery: turn toward the side with lower average obstacle cost
                if self._recovery_direction == 0.0:
                    cols = cost_map.shape[1]
                    left_cost = float(cost_map[:, :cols // 2].mean())
                    right_cost = float(cost_map[:, cols // 2:].mean())
                    self._recovery_direction = -self._RECOVERY_STEERING if left_cost < right_cost else self._RECOVERY_STEERING

                raw_steering = self._recovery_direction
                raw_speed = self._RECOVERY_SPEED
                direction_name = "LEFT" if self._recovery_direction < 0 else "RIGHT"
                reasoning = f"Recovery turn {direction_name} (blocked {self._consecutive_blocked} frames)"
                confidence = 0.3
            else:
                raw_steering = 0.0
                raw_speed = 0.0
                reasoning = "No feasible path — stopping"
                confidence = 1.0
        else:
            self._consecutive_blocked = 0
            self._recovery_direction = 0.0
            best = feasible[0]
            raw_steering = best.steering
            raw_speed = best.speed
            confidence = max(0.0, 1.0 - best.total_cost)
            reasoning = (
                f"steer={raw_steering:+.2f} spd={raw_speed:.2f} "
                f"cost={best.total_cost:.3f} "
                f"[obs={best.obstacle_cost:.2f} head={best.heading_cost:.2f} "
                f"smooth={best.smoothness_cost:.2f} clear={best.clearance_cost:.2f} "
                f"prog={best.progress_cost:.2f}]"
            )

        # 5. Temporal smoothing (EMA)
        # STOP bypasses smoothing
        if raw_speed < 0.05 and not feasible:
            smoothed_steering = 0.0
            smoothed_speed = 0.0
        else:
            smoothed_steering = EMA_ALPHA * raw_steering + (1 - EMA_ALPHA) * self._ema_steering
            smoothed_speed = EMA_ALPHA * raw_speed + (1 - EMA_ALPHA) * self._ema_speed

        self._ema_steering = smoothed_steering
        self._ema_speed = smoothed_speed

        # Update trajectory evaluator with latest command
        self._traj_eval.set_previous(smoothed_steering, smoothed_speed)

        # 6. Generate pixel-space trajectory for visualization
        traj_pixels = self._trajectory_to_pixels(smoothed_steering, smoothed_speed)

        latency_ms = (time.perf_counter() - t0) * 1000

        output = PlannerOutput(
            steering=smoothed_steering,
            speed=smoothed_speed,
            confidence=confidence,
            reasoning=reasoning,
            latency_ms=round(latency_ms, 2),
            feasible_count=feasible_count,
            nearest_obstacle_m=round(nearest_m, 3),
            trajectory_pixels=traj_pixels,
        )
        self._latest_output = output
        return output

    def map_to_direction(self, output: PlannerOutput) -> MotorDirection:
        """Convert continuous planner output to discrete MotorDirection.

        Applies time-based direction hold to prevent oscillation.
        """
        now = time.monotonic()

        # STOP always takes effect immediately
        if output.speed < 0.1 and output.feasible_count == 0:
            self._held_direction = MotorDirection.STOP
            self._hold_until = 0.0
            return MotorDirection.STOP

        # Determine raw direction
        if output.speed < 0.1:
            raw_dir = MotorDirection.STOP
        elif abs(output.steering) < 0.35:
            raw_dir = MotorDirection.FORWARD
        elif output.steering < 0:
            raw_dir = MotorDirection.LEFT
        else:
            raw_dir = MotorDirection.RIGHT

        # STOP always immediate
        if raw_dir == MotorDirection.STOP:
            self._held_direction = raw_dir
            self._hold_until = 0.0
            return raw_dir

        if self._held_direction is None or self._held_direction == MotorDirection.STOP:
            # First non-stop direction — accept immediately
            self._held_direction = raw_dir
            self._hold_until = now + DIRECTION_HOLD_SECONDS
            return raw_dir

        if raw_dir == self._held_direction:
            # Same direction — extend hold
            self._hold_until = now + DIRECTION_HOLD_SECONDS
            return raw_dir

        # Different direction — only change after hold expires
        if now >= self._hold_until:
            self._held_direction = raw_dir
            self._hold_until = now + DIRECTION_HOLD_SECONDS
            return raw_dir

        # Still holding previous direction
        return self._held_direction

    # ------------------------------------------------------------------
    # Trajectory visualisation helpers
    # ------------------------------------------------------------------
    def _world_to_grid(self, x_m: float, y_m: float) -> tuple[int, int]:
        """World metres → grid (row, col) for occupancy checks."""
        col = int((x_m + self._cfg.width_m / 2) / self._cfg.cell_size_m)
        row = int((self._cfg.depth_m - y_m) / self._cfg.cell_size_m)
        col = max(0, min(col, self._cfg.cols - 1))
        row = max(0, min(row, self._cfg.rows - 1))
        return (row, col)

    @staticmethod
    def _world_to_pixel(x_m: float, y_m: float) -> tuple[int, int]:
        """Convert world (x_m lateral, y_m forward) back to pixel coords.

        Inverse of the perspective mapping used in occupancy_grid / cv_pipeline.
        """
        vanish_y = int(_FRAME_H * _VANISH_Y_FRAC)
        # y_m → pixel row  (inverse: y_pixel = DIST_SCALE / y_m + vanish_y)
        y_m_clamped = max(y_m, 0.15)
        py = int(_DIST_SCALE / y_m_clamped + vanish_y)
        py = max(vanish_y, min(py, _FRAME_H - 1))
        # x_m → pixel col  (inverse of lateral mapping)
        # lateral_m = frac * (width_m/2) * scale, where scale = min(dist, depth)/depth
        # We approximate: px = cx + frac * (FRAME_W/2)
        # frac = x_m / ((width_m/2) * scale)
        depth_m = 4.0  # match GridConfig default
        scale = max(min(y_m_clamped, depth_m) / depth_m, 0.15)
        half_w_m = 1.5  # width_m / 2
        frac = x_m / (half_w_m * scale) if (half_w_m * scale) > 0.01 else 0.0
        frac = max(-1.0, min(frac, 1.0))
        px = int(_FRAME_W / 2 + frac * (_FRAME_W / 2))
        px = max(0, min(px, _FRAME_W - 1))
        return (px, py)

    def _trajectory_to_pixels(
        self, steering: float, speed: float, n_points: int = 20
    ) -> list[tuple[int, int]]:
        """Sample the smoothed trajectory arc and convert to pixel coords.

        Truncates the drawn path when it reaches:
        - the frame edge margin (prevents misleading border extension), or
        - an occupied grid cell (prevents line from visually crossing obstacles
          that are at a different depth but overlap in the 2D projection).
        """
        if speed < 0.05:
            return []

        from backend.services.path_planner.trajectory_evaluator import (
            LOOK_AHEAD_M, MIN_TURN_RADIUS_M, MAX_ARC_THETA,
        )

        arc_length = LOOK_AHEAD_M * min(speed, 1.0)
        pixels: list[tuple[int, int]] = []

        # Edge margin — stop drawing before the frame border
        edge_margin = 50
        # Occupancy threshold for visual truncation
        occ_threshold = 0.5
        grid = self._grid_builder._grid

        # Start at robot position (bottom-centre)
        pixels.append((_FRAME_W // 2, _FRAME_H - 1))

        if abs(steering) < 0.05:
            # Straight line
            for i in range(1, n_points + 1):
                y_m = arc_length * i / n_points
                px, py = self._world_to_pixel(0.0, y_m)
                # Check occupancy at this world point
                gr, gc = self._world_to_grid(0.0, y_m)
                if grid[gr, gc] > occ_threshold:
                    break
                pixels.append((px, py))
        else:
            radius = MIN_TURN_RADIUS_M / abs(steering)
            theta_total = min(arc_length / radius, MAX_ARC_THETA)
            sign = 1.0 if steering > 0 else -1.0

            for i in range(1, n_points + 1):
                theta = theta_total * i / n_points
                x_m = sign * radius * (1 - math.cos(theta))
                y_m = radius * math.sin(theta)
                px, py = self._world_to_pixel(x_m, y_m)
                # Truncate at frame edge
                if px < edge_margin or px > (_FRAME_W - edge_margin):
                    break
                # Truncate at occupied cells (prevents line crossing obstacles)
                gr, gc = self._world_to_grid(x_m, y_m)
                if grid[gr, gc] > occ_threshold:
                    break
                pixels.append((px, py))

        return pixels
