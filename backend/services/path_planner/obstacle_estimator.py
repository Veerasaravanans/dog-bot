"""Per-obstacle Kalman filter for smooth position/velocity estimation."""

import numpy as np

from backend.models.schemas import TrackedObstacle, Detection
from backend.services.path_planner.schemas import ObstacleState

# Camera constants (match cv_pipeline.py)
VANISH_Y_FRAC = 0.30
DIST_SCALE = 80.0
FRAME_W = 640
FRAME_H = 480

MAX_UNSEEN_FRAMES = 10


class _KalmanTrack:
    """Constant-velocity Kalman filter for one obstacle (4 states: x, y, vx, vy)."""

    def __init__(self, x: float, y: float):
        # State: [x, y, vx, vy]
        self.state = np.array([x, y, 0.0, 0.0], dtype=np.float64)
        # Covariance
        self.P = np.diag([0.1, 0.1, 1.0, 1.0])
        # Process noise
        self.Q = np.diag([0.01, 0.01, 0.1, 0.1])
        # Measurement noise
        self.R = np.diag([0.05, 0.05])
        # Transition (dt applied at predict time)
        self.F = np.eye(4, dtype=np.float64)
        # Observation: we measure x, y
        self.H = np.array([[1, 0, 0, 0],
                           [0, 1, 0, 0]], dtype=np.float64)

    def predict(self, dt: float = 1.0 / 15):
        self.F[0, 2] = dt
        self.F[1, 3] = dt
        self.state = self.F @ self.state
        self.P = self.F @ self.P @ self.F.T + self.Q

    def update(self, z_x: float, z_y: float):
        z = np.array([z_x, z_y])
        y = z - self.H @ self.state
        S = self.H @ self.P @ self.H.T + self.R
        K = self.P @ self.H.T @ np.linalg.inv(S)
        self.state = self.state + K @ y
        I4 = np.eye(4)
        self.P = (I4 - K @ self.H) @ self.P


class ObstacleStateEstimator:
    def __init__(self):
        self._tracks: dict[int, _KalmanTrack] = {}
        self._states: dict[int, ObstacleState] = {}
        self._class_names: dict[int, str] = {}
        self._radii: dict[int, float] = {}

    def _pixel_to_world(self, px: int, py: int) -> tuple[float, float]:
        vanish_y = int(FRAME_H * VANISH_Y_FRAC)
        eff_y = max(py, vanish_y + 1)
        dist_m = min(max(DIST_SCALE / (eff_y - vanish_y), 0.1), 10.0)
        lateral_m = (px - FRAME_W / 2) / (FRAME_W / 2) * 1.5
        lateral_m *= dist_m / 2.0
        return (lateral_m, dist_m)

    def update(
        self,
        obstacles: list[TrackedObstacle],
        detections: list[Detection],
        dt: float = 1.0 / 15,
    ) -> list[ObstacleState]:
        """Run one Kalman cycle: predict all, then update matched tracks."""

        seen_ids: set[int] = set()

        # --- Predict all existing tracks ---
        for kf in self._tracks.values():
            kf.predict(dt)

        # --- Update from CV tracked obstacles ---
        for obs in obstacles:
            tid = obs.id
            seen_ids.add(tid)
            cx, cy = obs.centroid
            wx, wy = self._pixel_to_world(cx, cy)

            if tid not in self._tracks:
                self._tracks[tid] = _KalmanTrack(wx, wy)
            self._tracks[tid].update(wx, wy)

            # Estimate radius from bbox width
            half_w = obs.bbox.w / 2
            r_m = (half_w / FRAME_W) * 3.0 * (wy / 2.0)
            self._radii[tid] = max(r_m, 0.05)

        # --- Enrich with ML detections (match by track_id) ---
        for det in detections:
            if det.track_id is not None:
                self._class_names[det.track_id] = det.class_name
                if det.track_id in seen_ids:
                    continue
                # ML-only detection with track_id
                tid = det.track_id
                seen_ids.add(tid)
                bcx = det.bbox.x + det.bbox.w // 2
                bcy = det.bbox.y + det.bbox.h // 2
                wx, wy = self._pixel_to_world(bcx, bcy)
                if tid not in self._tracks:
                    self._tracks[tid] = _KalmanTrack(wx, wy)
                self._tracks[tid].update(wx, wy)
                half_w = det.bbox.w / 2
                r_m = (half_w / FRAME_W) * 3.0 * (wy / 2.0)
                self._radii[tid] = max(r_m, 0.05)

        # --- Build output states, age out stale tracks ---
        result: list[ObstacleState] = []
        stale: list[int] = []

        for tid, kf in self._tracks.items():
            if tid in seen_ids:
                unseen = 0
            else:
                unseen = self._states[tid].frames_unseen + 1 if tid in self._states else 1

            if unseen > MAX_UNSEEN_FRAMES:
                stale.append(tid)
                continue

            st = kf.state
            obs_state = ObstacleState(
                track_id=tid,
                x=float(st[0]),
                y=float(st[1]),
                vx=float(st[2]),
                vy=float(st[3]),
                radius=self._radii.get(tid, 0.1),
                confidence=1.0 if tid in seen_ids else max(0.0, 1.0 - unseen * 0.1),
                class_name=self._class_names.get(tid, ""),
                frames_unseen=unseen,
            )
            self._states[tid] = obs_state
            result.append(obs_state)

        for tid in stale:
            self._tracks.pop(tid, None)
            self._states.pop(tid, None)
            self._class_names.pop(tid, None)
            self._radii.pop(tid, None)

        return result

    def predict_future(self, obs: ObstacleState, dt: float) -> tuple[float, float]:
        """Predict obstacle position at time `dt` seconds in the future."""
        return (obs.x + obs.vx * dt, obs.y + obs.vy * dt)
