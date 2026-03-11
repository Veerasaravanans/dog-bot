"""Builds a bird's-eye occupancy grid from CV obstacles and ML detections."""

import numpy as np
import cv2

from backend.models.schemas import TrackedObstacle, Detection
from backend.services.path_planner.schemas import GridConfig

# Camera constants (match cv_pipeline.py)
VANISH_Y_FRAC = 0.30
DIST_SCALE = 80.0
FRAME_W = 640
FRAME_H = 480


class OccupancyGridBuilder:
    def __init__(self, config: GridConfig):
        self._cfg = config
        self._grid = np.zeros(config.shape, dtype=np.float32)
        self._decay = 0.5
        # Safety inflation kernel (Gaussian, ~5 cm radius → 1 cell)
        ksize = 3
        self._inflate_kernel = cv2.getGaussianKernel(ksize, 0.8)
        self._inflate_kernel_2d = self._inflate_kernel @ self._inflate_kernel.T
        # Precompute pixel-y → distance lookup (480 rows)
        self._y_to_dist = self._build_y_lookup()

    def _build_y_lookup(self) -> np.ndarray:
        """Precompute distance for each pixel row."""
        vanish_y = int(FRAME_H * VANISH_Y_FRAC)
        lut = np.full(FRAME_H, 10.0, dtype=np.float32)
        for y in range(vanish_y + 1, FRAME_H):
            pixel_delta = y - vanish_y
            dist = DIST_SCALE / pixel_delta
            lut[y] = max(0.1, min(dist, 10.0))
        return lut

    def _pixel_to_world(self, px: int, py: int) -> tuple[float, float]:
        """Convert pixel coordinates to world (x_m, y_m).

        World frame: x = lateral (0 at centre, positive right),
                     y = forward distance from robot.
        """
        dist_m = self._y_to_dist[min(py, FRAME_H - 1)]
        # Lateral offset: pixel x relative to image centre, scaled by distance
        # Use a perspective scaling factor that grows with distance but is
        # clamped to prevent extreme lateral spread for close objects.
        frac = (px - FRAME_W / 2) / (FRAME_W / 2)  # -1 to +1
        scale = min(dist_m, self._cfg.depth_m) / self._cfg.depth_m
        lateral_m = frac * (self._cfg.width_m / 2) * max(scale, 0.15)
        return (lateral_m, dist_m)

    def _world_to_grid(self, x_m: float, y_m: float) -> tuple[int, int]:
        """World metres → grid (row, col).

        Row 0 = farthest (depth_m), row max = closest (0 m).
        Col 0 = left edge, col max = right edge.
        """
        col = int((x_m + self._cfg.width_m / 2) / self._cfg.cell_size_m)
        row = int((self._cfg.depth_m - y_m) / self._cfg.cell_size_m)
        col = max(0, min(col, self._cfg.cols - 1))
        row = max(0, min(row, self._cfg.rows - 1))
        return (row, col)

    def _project_lowconf_mask(self, mask: np.ndarray, confidence: float):
        """Project a downsampled (4×) binary mask onto the grid at low confidence.

        Used for thin-edge and floor-anomaly layers.  Fully vectorized for speed.
        """
        ys, xs = np.nonzero(mask)
        if len(ys) == 0:
            return

        vanish_y = int(FRAME_H * VANISH_Y_FRAC)

        # Scale back to full-frame pixel coordinates
        full_x = (xs * 4).astype(np.float64)
        full_y = (ys * 4 + vanish_y).astype(np.int32)
        full_y = np.clip(full_y, vanish_y + 1, FRAME_H - 1)

        # Vectorized pixel → world
        dist_m = self._y_to_dist[full_y]
        frac = (full_x - FRAME_W / 2) / (FRAME_W / 2)
        scale = np.minimum(dist_m, self._cfg.depth_m) / self._cfg.depth_m
        scale = np.maximum(scale, 0.15)
        lateral_m = frac * (self._cfg.width_m / 2) * scale

        # Vectorized world → grid
        col = ((lateral_m + self._cfg.width_m / 2) / self._cfg.cell_size_m).astype(np.int32)
        row = ((self._cfg.depth_m - dist_m) / self._cfg.cell_size_m).astype(np.int32)
        col = np.clip(col, 0, self._cfg.cols - 1)
        row = np.clip(row, 0, self._cfg.rows - 1)

        # Set cells — use np.maximum.at to avoid overwriting higher-confidence data
        np.maximum.at(self._grid, (row, col), confidence)

    def update(
        self,
        obstacles: list[TrackedObstacle],
        detections: list[Detection],
        edge_map: np.ndarray | None = None,
        floor_obstacle_mask: np.ndarray | None = None,
    ) -> np.ndarray:
        """Build occupancy grid for current frame.

        Returns float32 array, shape (rows, cols), values 0.0–1.0.
        """
        # Temporal decay — fade old occupancy
        self._grid *= self._decay

        # --- Project CV tracked obstacles ---
        for obs in obstacles:
            # Only project confirmed obstacles (3+ frames) at full confidence.
            # Newer detections get low confidence — influences cost map
            # but won't block paths alone.
            confidence = 1.0 if obs.frames_seen >= 3 else 0.2
            cx, cy = obs.centroid
            bx, by, bw, bh = obs.bbox.x, obs.bbox.y, obs.bbox.w, obs.bbox.h

            # Project four bbox corners to world, then to grid polygon
            # Clamp to frame bounds to prevent incorrect world projections
            corners_px = [
                (max(0, min(bx, FRAME_W-1)), max(0, min(by + bh, FRAME_H-1))),
                (max(0, min(bx + bw, FRAME_W-1)), max(0, min(by + bh, FRAME_H-1))),
                (max(0, min(bx + bw, FRAME_W-1)), max(0, min(by, FRAME_H-1))),
                (max(0, min(bx, FRAME_W-1)), max(0, min(by, FRAME_H-1))),
            ]
            grid_pts = []
            for px, py in corners_px:
                wx, wy = self._pixel_to_world(int(px), int(py))
                gr, gc = self._world_to_grid(wx, wy)
                grid_pts.append([gc, gr])  # cv2 expects (x, y) = (col, row)

            if grid_pts:
                poly = np.array([grid_pts], dtype=np.int32)
                cv2.fillPoly(self._grid, poly, confidence)

        # --- Project ML detection masks ---
        for det in detections:
            if det.mask and len(det.mask) >= 3:
                grid_pts = []
                for point in det.mask:
                    if len(point) >= 2:
                        px_clamped = max(0, min(int(point[0]), FRAME_W - 1))
                        py_clamped = max(0, min(int(point[1]), FRAME_H - 1))
                        wx, wy = self._pixel_to_world(px_clamped, py_clamped)
                        gr, gc = self._world_to_grid(wx, wy)
                        grid_pts.append([gc, gr])
                if len(grid_pts) >= 3:
                    poly = np.array([grid_pts], dtype=np.int32)
                    cv2.fillPoly(self._grid, poly, 1.0)
            else:
                # Fallback: use bounding box
                bb = det.bbox
                corners_px = [
                    (max(0, min(bb.x, FRAME_W-1)), max(0, min(bb.y + bb.h, FRAME_H-1))),
                    (max(0, min(bb.x + bb.w, FRAME_W-1)), max(0, min(bb.y + bb.h, FRAME_H-1))),
                    (max(0, min(bb.x + bb.w, FRAME_W-1)), max(0, min(bb.y, FRAME_H-1))),
                    (max(0, min(bb.x, FRAME_W-1)), max(0, min(bb.y, FRAME_H-1))),
                ]
                grid_pts = []
                for px, py in corners_px:
                    wx, wy = self._pixel_to_world(int(px), int(py))
                    gr, gc = self._world_to_grid(wx, wy)
                    grid_pts.append([gc, gr])
                if grid_pts:
                    poly = np.array([grid_pts], dtype=np.int32)
                    cv2.fillPoly(self._grid, poly, 0.8)

        # --- Project auxiliary CV maps (thin edges, floor anomalies) ---
        # Low confidence values prevent these from triggering infeasibility alone.
        # Persistent signals accumulate via temporal decay to influence cost map.
        if edge_map is not None:
            self._project_lowconf_mask(edge_map, confidence=0.08)
        if floor_obstacle_mask is not None:
            self._project_lowconf_mask(floor_obstacle_mask, confidence=0.06)

        # Gaussian inflation for safety margins (only inflate, never reduce)
        inflated = cv2.filter2D(self._grid, -1, self._inflate_kernel_2d)
        # Scale inflated values down so they add a halo, not a full-strength copy
        inflated *= 0.5
        self._grid = np.clip(np.maximum(self._grid, inflated), 0.0, 1.0)

        return self._grid

    def reset(self):
        self._grid[:] = 0.0
