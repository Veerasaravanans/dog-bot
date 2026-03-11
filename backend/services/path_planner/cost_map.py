"""Generates a continuous cost surface from occupancy + obstacle states."""

import numpy as np

from backend.services.path_planner.schemas import GridConfig, ObstacleState
from backend.services.path_planner.obstacle_estimator import ObstacleStateEstimator

# Cost component weights (must sum to 1.0)
W_OCCUPANCY = 0.45
W_REPULSIVE = 0.25
W_PREDICTIVE = 0.15
W_BOUNDARY = 0.05
W_ATTRACTIVE = 0.10

# Prediction horizons in seconds
PREDICT_HORIZONS = (0.3, 0.6, 1.0)

# Repulsive field decay (cells)
REPULSIVE_SIGMA = 4.0


class CostMapGenerator:
    def __init__(self, config: GridConfig):
        self._cfg = config
        # Precompute static boundary cost (higher near edges)
        self._boundary_cost = self._build_boundary_cost()
        # Precompute static attractive cost (lower at forward-centre)
        self._attractive_cost = self._build_attractive_cost()
        # Coordinate grids for vectorised distance computation
        rows, cols = config.shape
        self._row_grid, self._col_grid = np.mgrid[0:rows, 0:cols]

    def _build_boundary_cost(self) -> np.ndarray:
        rows, cols = self._cfg.shape
        cost = np.zeros((rows, cols), dtype=np.float32)
        for c in range(cols):
            dist_left = c
            dist_right = cols - 1 - c
            edge_dist = min(dist_left, dist_right)
            # Normalise: 0 at centre, 1 at edge
            cost[:, c] = 1.0 - (edge_dist / (cols / 2))
        return np.clip(cost, 0.0, 1.0)

    def _build_attractive_cost(self) -> np.ndarray:
        """Lower cost toward forward-centre (row 0 = far, high row = close to robot)."""
        rows, cols = self._cfg.shape
        cost = np.zeros((rows, cols), dtype=np.float32)
        centre_col = cols / 2
        for r in range(rows):
            for c in range(cols):
                # Forward progress: prefer lower rows (farther ahead)
                fwd = r / rows  # 0 at far, 1 at near
                # Centre bias
                lat = abs(c - centre_col) / centre_col
                cost[r, c] = 0.6 * fwd + 0.4 * lat
        return cost.astype(np.float32)

    def _repulsive_field(self, cx_m: float, cy_m: float) -> np.ndarray:
        """Exponential decay repulsive field centred at world (cx, cy)."""
        col = (cx_m + self._cfg.width_m / 2) / self._cfg.cell_size_m
        row = (self._cfg.depth_m - cy_m) / self._cfg.cell_size_m
        dist_sq = (self._row_grid - row) ** 2 + (self._col_grid - col) ** 2
        return np.exp(-dist_sq / (2 * REPULSIVE_SIGMA ** 2)).astype(np.float32)

    def generate(
        self,
        occupancy: np.ndarray,
        obstacle_states: list[ObstacleState],
        estimator: ObstacleStateEstimator,
    ) -> np.ndarray:
        """Produce a normalised cost map (0 = best, 1 = worst).

        Parameters
        ----------
        occupancy : 2-D float32 array from OccupancyGridBuilder
        obstacle_states : filtered obstacle list from ObstacleStateEstimator
        estimator : estimator instance (for predict_future)
        """
        shape = self._cfg.shape

        # 1. Occupancy component
        occ_cost = occupancy  # already 0–1

        # 2. Repulsive potential around current obstacle centroids
        rep_cost = np.zeros(shape, dtype=np.float32)
        for obs in obstacle_states:
            rep_cost += self._repulsive_field(obs.x, obs.y) * obs.confidence

        # 3. Predictive cost at future obstacle positions
        pred_cost = np.zeros(shape, dtype=np.float32)
        for obs in obstacle_states:
            for t in PREDICT_HORIZONS:
                fx, fy = estimator.predict_future(obs, t)
                # Weaker influence for farther predictions
                weight = 1.0 / (1.0 + t)
                pred_cost += self._repulsive_field(fx, fy) * weight * obs.confidence

        # Normalise variable components to [0, 1]
        if rep_cost.max() > 0:
            rep_cost /= rep_cost.max()
        if pred_cost.max() > 0:
            pred_cost /= pred_cost.max()

        # Weighted combination
        total = (
            W_OCCUPANCY * occ_cost
            + W_REPULSIVE * rep_cost
            + W_PREDICTIVE * pred_cost
            + W_BOUNDARY * self._boundary_cost
            + W_ATTRACTIVE * self._attractive_cost
        )

        return np.clip(total, 0.0, 1.0)
