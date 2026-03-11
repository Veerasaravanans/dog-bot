"""
Advanced CV Pipeline — Car Reverse Camera Style
- Ground-plane obstacle detection (ignores faces, walls, ceiling)
- Perspective-based distance estimation
- Lane-based free-path analysis
- Multi-frame obstacle tracking with IDs
- Reverse-camera trapezoidal grid with distance markings
"""

import cv2
import numpy as np
import logging

from backend.config import settings
from backend.models.schemas import TrackedObstacle, BBox, LaneStatus

logger = logging.getLogger("dogbot.cv")

# ═══════════════════════════════════════════════════════════════
# CALIBRATION
# ═══════════════════════════════════════════════════════════════
CAM_HEIGHT_M = 0.15
VANISH_Y_FRAC = 0.30
DIST_SCALE = 80.0

ZONE_NEAR_M = 0.5
ZONE_MID_M = 1.2

MIN_OBSTACLE_FRAC = 0.003
MAX_OBSTACLE_FRAC = 0.40

LANE_LEFT_END = 0.33
LANE_RIGHT_START = 0.67

TRACK_MAX_DIST = 80
TRACK_MAX_AGE = 8


class ObstacleTracker:
    """Simple centroid-based multi-object tracker."""

    def __init__(self):
        self._next_id = 1
        self._tracks: dict[int, dict] = {}

    def update(self, centroids: list[tuple[int, int]]) -> dict[int, tuple[int, int]]:
        if not centroids:
            self._age_all()
            return {}

        if not self._tracks:
            result = {}
            for cx, cy in centroids:
                tid = self._next_id
                self._next_id += 1
                self._tracks[tid] = {"cx": cx, "cy": cy, "age": 0, "seen": 1,
                                     "prev_cx": cx, "prev_cy": cy}
                result[tid] = (cx, cy)
            return result

        used_tracks = set()
        used_dets = set()
        result = {}
        track_ids = list(self._tracks.keys())

        pairs = []
        for ti, tid in enumerate(track_ids):
            t = self._tracks[tid]
            for di, (cx, cy) in enumerate(centroids):
                d = ((t["cx"] - cx) ** 2 + (t["cy"] - cy) ** 2) ** 0.5
                pairs.append((d, ti, di, tid, cx, cy))
        pairs.sort()

        for dist, ti, di, tid, cx, cy in pairs:
            if tid in used_tracks or di in used_dets:
                continue
            if dist > TRACK_MAX_DIST:
                continue
            t = self._tracks[tid]
            t["prev_cx"], t["prev_cy"] = t["cx"], t["cy"]
            t["cx"], t["cy"] = cx, cy
            t["age"] = 0
            t["seen"] += 1
            used_tracks.add(tid)
            used_dets.add(di)
            result[tid] = (cx, cy)

        for di, (cx, cy) in enumerate(centroids):
            if di not in used_dets:
                tid = self._next_id
                self._next_id += 1
                self._tracks[tid] = {"cx": cx, "cy": cy, "age": 0, "seen": 1,
                                     "prev_cx": cx, "prev_cy": cy}
                result[tid] = (cx, cy)

        for tid in track_ids:
            if tid not in used_tracks:
                self._tracks[tid]["age"] += 1

        dead = [tid for tid, t in self._tracks.items() if t["age"] > TRACK_MAX_AGE]
        for tid in dead:
            del self._tracks[tid]

        return result

    def get_velocity(self, tid: int) -> tuple[float, float]:
        t = self._tracks.get(tid)
        if t is None:
            return (0.0, 0.0)
        return (float(t["cx"] - t["prev_cx"]), float(t["cy"] - t["prev_cy"]))

    def get_frames_seen(self, tid: int) -> int:
        t = self._tracks.get(tid)
        return t["seen"] if t else 1

    def _age_all(self):
        for t in self._tracks.values():
            t["age"] += 1
        dead = [tid for tid, t in self._tracks.items() if t["age"] > TRACK_MAX_AGE]
        for tid in dead:
            del self._tracks[tid]


class CVPipeline:
    def __init__(self):
        self._kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        self._kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self._bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=300, varThreshold=40, detectShadows=True
        )
        self._tracker = ObstacleTracker()
        self._frame_count = 0
        
        # Optical flow tracking
        self._prev_gray = None
        self._flow_threshold = 2.0  # pixels/frame for motion detection
        self._last_motion_mask: np.ndarray | None = None
        
        # Enhanced depth calibration
        self._vanishing_point = None
        self._depth_calibration_factor = 1.0

        # Floor color model (adaptive EMA)
        self._floor_h_mean = 0.0
        self._floor_h_std = 30.0
        self._floor_s_mean = 0.0
        self._floor_s_std = 30.0
        self._floor_v_mean = 128.0
        self._floor_v_std = 30.0
        self._floor_model_initialized = False

        # Thin edge detection kernels
        self._kernel_thin_close = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        self._kernel_thin_erode = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))

        # Latest auxiliary maps for path planner
        self._latest_edge_map: np.ndarray | None = None
        self._latest_floor_mask: np.ndarray | None = None

        self.RED = (0, 0, 255)
        self.YELLOW = (0, 200, 255)
        self.GREEN = (0, 255, 100)
        self.CYAN = (255, 200, 0)
        self.WHITE = (220, 220, 220)
        self.DIM_GREEN = (0, 160, 80)

    # ═══════════════════════════════════════════════════════════════
    # DISTANCE ESTIMATION (ENHANCED)
    # ═══════════════════════════════════════════════════════════════
    def _detect_vanishing_point(self, edges: np.ndarray, h: int, w: int) -> tuple[int, int]:
        """Auto-detect vanishing point from edge lines for perspective calibration."""
        # Use probabilistic Hough transform to find lines
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=50,
                                minLineLength=30, maxLineGap=10)
        
        if lines is None or len(lines) < 5:
            # Fall back to default vanishing point
            return (w // 2, int(h * VANISH_Y_FRAC))
        
        # Find intersection points of lines to estimate vanishing point
        intersections = []
        for i in range(min(len(lines), 20)):
            for j in range(i + 1, min(len(lines), 20)):
                x1, y1, x2, y2 = lines[i][0]
                x3, y3, x4, y4 = lines[j][0]
                
                # Calculate intersection
                denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
                if abs(denom) < 1e-6:
                    continue
                
                px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / denom
                py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / denom
                
                # Only keep reasonable points (upper third of image)
                if 0 <= px < w and 0 <= py < h * 0.5:
                    intersections.append((int(px), int(py)))
        
        if not intersections:
            return (w // 2, int(h * VANISH_Y_FRAC))
        
        # Cluster and find median (robust to outliers)
        intersections = np.array(intersections)
        vx = int(np.median(intersections[:, 0]))
        vy = int(np.median(intersections[:, 1]))
        
        return (vx, vy)
    
    def _pixel_to_distance(self, y: int, h: int) -> float:
        """Enhanced perspective-based distance estimation."""
        vanish_y = int(h * VANISH_Y_FRAC)
        if self._vanishing_point:
            vanish_y = min(vanish_y, self._vanishing_point[1])
        
        effective_y = max(y, vanish_y + 1)
        
        # Improved inverse perspective mapping
        # Account for camera height and angle
        pixel_delta = effective_y - vanish_y
        dist = (DIST_SCALE * self._depth_calibration_factor) / pixel_delta
        
        return round(max(0.1, min(dist, 10.0)), 2)

    def _distance_to_pixel_y(self, dist_m: float, frame_h: int) -> int:
        vanish_y = int(frame_h * VANISH_Y_FRAC)
        y = int(DIST_SCALE / dist_m + vanish_y)
        return min(max(y, vanish_y), frame_h - 1)

    # ═══════════════════════════════════════════════════════════════
    # ZONE + LANE CLASSIFICATION
    # ═══════════════════════════════════════════════════════════════
    def _classify_zone(self, dist: float) -> str:
        """Classify safety zone based on distance."""
        if dist <= ZONE_NEAR_M:
            return "DANGER"
        elif dist <= ZONE_MID_M:
            return "CAUTION"
        return "SAFE"
    
    def _calculate_ttc(self, distance_m: float, velocity_py: tuple[float, float]) -> float:
        """Calculate Time-To-Collision in seconds."""
        # velocity_py is in pixels per frame, convert to m/s
        # Assuming ~10 FPS and rough calibration
        closing_speed_m_s = abs(velocity_py[1]) * 0.005  # rough estimate
        
        if closing_speed_m_s < 0.01:  # stationary
            return 999.0
        
        ttc = distance_m / closing_speed_m_s
        return min(ttc, 999.0)

    def _classify_lane(self, cx: int, w: int) -> str:
        frac = cx / w
        if frac < LANE_LEFT_END:
            return "LEFT"
        elif frac > LANE_RIGHT_START:
            return "RIGHT"
        return "CENTER"

    def _zone_color(self, zone: str) -> tuple:
        if zone == "NEAR":
            return self.RED
        elif zone == "MID":
            return self.YELLOW
        return self.GREEN

    # ═══════════════════════════════════════════════════════════════
    # TEXTURE-AGNOSTIC DETECTION (ENHANCED)
    # ═══════════════════════════════════════════════════════════════
    def _compute_gradient_magnitude(self, gray: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Compute gradient magnitude using Sobel operators for texture-agnostic detection.

        Returns (gradient_mask, grad_x, grad_y) — the raw Sobel components are
        reused by _extract_thin_edge_map to filter for vertical structures.
        """
        grad_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)

        magnitude = np.sqrt(grad_x**2 + grad_y**2)
        magnitude = np.uint8(np.clip(magnitude, 0, 255))

        _, gradient_mask = cv2.threshold(magnitude, settings.cv_edge_gradient_threshold, 255, cv2.THRESH_BINARY)

        return gradient_mask, grad_x, grad_y
    
    def _compute_optical_flow(self, gray: np.ndarray) -> np.ndarray:
        """Detect motion using dense optical flow for moving obstacle detection."""
        if self._prev_gray is None:
            self._prev_gray = gray.copy()
            return np.zeros_like(gray)
        
        # Calculate dense optical flow
        flow = cv2.calcOpticalFlowFarneback(
            self._prev_gray, gray, None,
            pyr_scale=0.5, levels=3, winsize=15,
            iterations=3, poly_n=5, poly_sigma=1.2, flags=0
        )
        
        # Compute flow magnitude
        magnitude = np.sqrt(flow[..., 0]**2 + flow[..., 1]**2)
        
        # Threshold for significant motion
        motion_mask = np.uint8((magnitude > self._flow_threshold) * 255)
        
        # Update previous frame
        self._prev_gray = gray.copy()
        
        return motion_mask

    # ═══════════════════════════════════════════════════════════════
    # THIN EDGE & FLOOR PLANE DETECTION (for occupancy grid)
    # ═══════════════════════════════════════════════════════════════
    def _extract_thin_edge_map(
        self, grad_x: np.ndarray, grad_y: np.ndarray, roi_h: int, roi_w: int
    ) -> np.ndarray:
        """Extract thin vertical structures from Sobel gradients.

        Desk legs, chair legs, poles etc. produce strong *horizontal* gradients
        (because the edge runs vertically).  We threshold at a higher magnitude
        than the general detector and keep only near-vertical edges to reject
        floor texture lines (tile grout, carpet patterns).

        Returns a downsampled (4×) binary mask, uint8 (255 = edge).
        """
        from backend.config import settings

        abs_gx = np.abs(grad_x)
        abs_gy = np.abs(grad_y)
        magnitude = np.sqrt(grad_x**2 + grad_y**2)

        # High threshold (80) to reject floor texture and shadow edges
        strong = magnitude > 80

        # Keep only near-vertical structures: |grad_x| > 2*|grad_y|
        vertical = abs_gx > 2.0 * abs_gy

        thin_edges = np.uint8((strong & vertical) * 255)

        # Connect thin fragments, then thin back down
        thin_edges = cv2.morphologyEx(thin_edges, cv2.MORPH_CLOSE, self._kernel_thin_close)
        thin_edges = cv2.erode(thin_edges, self._kernel_thin_erode, iterations=1)

        # Remove tiny noise components — keep only clusters ≥ 20 pixels
        contours, _ = cv2.findContours(thin_edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        thin_edges[:] = 0
        for c in contours:
            if cv2.contourArea(c) >= 20:
                cv2.drawContours(thin_edges, [c], -1, 255, -1)

        # Downsample 4× for efficient grid projection
        ds_h, ds_w = roi_h // 4, roi_w // 4
        if ds_h > 0 and ds_w > 0:
            thin_edges = cv2.resize(thin_edges, (ds_w, ds_h), interpolation=cv2.INTER_AREA)
            _, thin_edges = cv2.threshold(thin_edges, 127, 255, cv2.THRESH_BINARY)

        return thin_edges

    def _detect_floor_mask(
        self, roi_bgr: np.ndarray, roi_h: int, roi_w: int
    ) -> np.ndarray:
        """Adaptive floor color detection — flags non-floor pixels.

        Only analyzes the bottom 60% of the ROI (near field) where the ground
        plane model is reliable.  Far-field pixels are left as zero (no obstacle).

        Returns a downsampled (4×) binary mask, uint8 (255 = non-floor).
        """
        from backend.config import settings

        ds_h, ds_w = max(1, roi_h // 4), max(1, roi_w // 4)

        # Only analyze bottom 60% of ROI — far field has unreliable perspective
        near_start = int(roi_h * 0.4)
        near_roi = roi_bgr[near_start:, :]
        near_h = roi_h - near_start
        if near_h < 10:
            return np.zeros((ds_h, ds_w), dtype=np.uint8)

        roi_hsv = cv2.cvtColor(near_roi, cv2.COLOR_BGR2HSV)

        # Sample region: bottom-centre strip (known floor)
        sample_y1 = max(0, near_h - 30)
        sample_x1 = max(0, roi_w // 2 - 50)
        sample_x2 = min(roi_w, roi_w // 2 + 50)
        sample = roi_hsv[sample_y1:near_h, sample_x1:sample_x2]

        if sample.size == 0:
            return np.zeros((ds_h, ds_w), dtype=np.uint8)

        # Per-channel statistics of the floor sample
        s_h = sample[:, :, 0].astype(np.float32)
        s_s = sample[:, :, 1].astype(np.float32)
        s_v = sample[:, :, 2].astype(np.float32)

        h_mean, h_std = float(s_h.mean()), max(float(s_h.std()), 5.0)
        s_mean, s_std = float(s_s.mean()), max(float(s_s.std()), 5.0)
        v_mean, v_std = float(s_v.mean()), max(float(s_v.std()), 5.0)

        alpha = settings.cv_floor_ema_alpha
        if not self._floor_model_initialized:
            self._floor_h_mean = h_mean
            self._floor_h_std = h_std
            self._floor_s_mean = s_mean
            self._floor_s_std = s_std
            self._floor_v_mean = v_mean
            self._floor_v_std = v_std
            self._floor_model_initialized = True
        else:
            self._floor_h_mean = alpha * h_mean + (1 - alpha) * self._floor_h_mean
            self._floor_h_std = alpha * h_std + (1 - alpha) * self._floor_h_std
            self._floor_s_mean = alpha * s_mean + (1 - alpha) * self._floor_s_mean
            self._floor_s_std = alpha * s_std + (1 - alpha) * self._floor_s_std
            self._floor_v_mean = alpha * v_mean + (1 - alpha) * self._floor_v_mean
            self._floor_v_std = alpha * v_std + (1 - alpha) * self._floor_v_std

        k = settings.cv_floor_deviation_k
        lower = np.array([
            max(0, self._floor_h_mean - k * self._floor_h_std),
            max(0, self._floor_s_mean - k * self._floor_s_std),
            max(0, self._floor_v_mean - k * self._floor_v_std),
        ], dtype=np.uint8)
        upper = np.array([
            min(179, self._floor_h_mean + k * self._floor_h_std),
            min(255, self._floor_s_mean + k * self._floor_s_std),
            min(255, self._floor_v_mean + k * self._floor_v_std),
        ], dtype=np.uint8)

        floor_mask = cv2.inRange(roi_hsv, lower, upper)
        non_floor = cv2.bitwise_not(floor_mask)

        # Aggressive noise removal (9×9 open) + fill gaps (7×7 close)
        _floor_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        non_floor = cv2.morphologyEx(non_floor, cv2.MORPH_OPEN, _floor_open)
        non_floor = cv2.morphologyEx(non_floor, cv2.MORPH_CLOSE, self._kernel_close)

        # Pad zeros for the top 40% (far field — no floor detection there)
        full_mask = np.zeros((roi_h, roi_w), dtype=np.uint8)
        full_mask[near_start:, :] = non_floor

        # Downsample 4×
        if ds_h > 0 and ds_w > 0:
            full_mask = cv2.resize(full_mask, (ds_w, ds_h), interpolation=cv2.INTER_AREA)
            _, full_mask = cv2.threshold(full_mask, 127, 255, cv2.THRESH_BINARY)

        return full_mask

    def _assess_threat_level(self, distance_m: float, ttc: float, velocity: tuple[float, float]) -> int:
        """
        Assess threat level using multi-criteria analysis.
        Returns: 0=safe, 1=low, 2=medium, 3=high, 4=critical
        """
        # Criteria 1: Distance-based threat
        if distance_m < 0.3:
            dist_threat = 4  # Critical
        elif distance_m < 0.5:
            dist_threat = 3  # High
        elif distance_m < 1.0:
            dist_threat = 2  # Medium
        elif distance_m < 1.5:
            dist_threat = 1  # Low
        else:
            dist_threat = 0  # Safe
        
        # Criteria 2: Time-to-collision based threat
        if ttc < 1.0:
            ttc_threat = 4  # Critical - less than 1 second
        elif ttc < 2.0:
            ttc_threat = 3  # High
        elif ttc < 4.0:
            ttc_threat = 2  # Medium
        elif ttc < 8.0:
            ttc_threat = 1  # Low
        else:
            ttc_threat = 0  # Safe
        
        # Criteria 3: Velocity-based threat (approaching objects more dangerous)
        vel_magnitude = (velocity[0]**2 + velocity[1]**2) ** 0.5
        approaching = velocity[1] > 0  # positive Y velocity means approaching
        
        if approaching and vel_magnitude > 5.0:
            vel_threat = 2  # Fast approaching
        elif approaching and vel_magnitude > 2.0:
            vel_threat = 1  # Slowly approaching
        else:
            vel_threat = 0  # Stationary or moving away
        
        # Combined threat: take the maximum (most conservative)
        threat_level = max(dist_threat, ttc_threat) + vel_threat
        
        # Cap at 4 (critical)
        return min(threat_level, 4)

    # ═══════════════════════════════════════════════════════════════
    # OBSTACLE DETECTION (MULTI-SENSOR FUSION)
    # ═══════════════════════════════════════════════════════════════
    def detect_obstacles(self, frame: np.ndarray) -> list[TrackedObstacle]:
        """Enhanced multi-sensor obstacle detection with automotive-grade accuracy."""
        h, w = frame.shape[:2]
        vanish_y = int(h * VANISH_Y_FRAC)

        # Define ROI (region of interest: ground plane only)
        roi = frame[vanish_y:, :]
        roi_h, roi_w = roi.shape[:2]
        roi_area = roi_h * roi_w

        # Convert to grayscale for processing
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)

        # ═══ MULTI-SENSOR FUSION ═══
        
        # Sensor 1: Background subtraction (motion/new objects)
        fg_mask = self._bg_subtractor.apply(roi)
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, self._kernel_open)
        fg_mask[fg_mask == 127] = 0  # Remove shadows

        # Sensor 2: Enhanced edge detection (Canny)
        edges = cv2.Canny(blurred, settings.cv_canny_low, settings.cv_canny_high)
        edges = cv2.dilate(edges, self._kernel_close, iterations=2)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, self._kernel_close)
        
        # Sensor 3: Gradient magnitude (texture-agnostic boundaries)
        gradient_mask, grad_x, grad_y = self._compute_gradient_magnitude(gray)
        gradient_mask = cv2.dilate(gradient_mask, self._kernel_open, iterations=1)

        # Sensor 4: Optical flow (motion detection) — skip on odd frames for performance
        if self._frame_count % 2 == 0:
            motion_mask = self._compute_optical_flow(gray)
            motion_mask = cv2.morphologyEx(motion_mask, cv2.MORPH_CLOSE, self._kernel_close)
            self._last_motion_mask = motion_mask
        else:
            motion_mask = self._last_motion_mask if self._last_motion_mask is not None else np.zeros_like(gray)

        # Auto-detect vanishing point every 30 frames for depth calibration
        if self._frame_count % 30 == 0:
            self._vanishing_point = self._detect_vanishing_point(edges, roi_h, roi_w)

        # ═══ AUXILIARY MAPS for occupancy grid ═══
        # Thin vertical edge map (desk legs, chair legs, poles)
        self._latest_edge_map = self._extract_thin_edge_map(grad_x, grad_y, roi_h, roi_w)
        # Non-floor pixel mask (floor-colored obstacle detection)
        self._latest_floor_mask = self._detect_floor_mask(roi, roi_h, roi_w)

        # ═══ FUSION: Require at least 2 sensors to agree ═══
        # Count how many sensors fire per pixel (0-4).
        # A single sensor alone (shadow, floor edge) is ignored.
        sensor_sum = (
            (fg_mask > 0).astype(np.uint8) +
            (edges > 0).astype(np.uint8) +
            (gradient_mask > 0).astype(np.uint8) +
            (motion_mask > 0).astype(np.uint8)
        )
        combined = np.uint8((sensor_sum >= 2) * 255)
        
        # Final cleanup
        combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, self._kernel_close)
        combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN, self._kernel_open)

        # Find contours
        contours, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)

        min_area = roi_area * MIN_OBSTACLE_FRAC
        max_area = roi_area * MAX_OBSTACLE_FRAC

        raw_centroids = []
        raw_data = []

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < min_area or area > max_area:
                continue

            x, y, bw, bh = cv2.boundingRect(contour)

            # Filter by aspect ratio (reject very thin lines)
            aspect = bw / max(bh, 1)
            if aspect < 0.15 or aspect > 8.0:
                continue

            # Filter by vertical position (must be in lower part of ROI)
            bottom_y_in_roi = y + bh
            if bottom_y_in_roi < roi_h * 0.15:
                continue

            # Calculate centroid
            M = cv2.moments(contour)
            if M["m00"] == 0:
                continue
            cx = int(M["m10"] / M["m00"])
            cy = int(M["m01"] / M["m00"])

            raw_centroids.append((cx, cy + vanish_y))
            raw_data.append((
                BBox(x=x, y=y + vanish_y, w=bw, h=bh),
                float(area),
                cx,
                cy + vanish_y
            ))

        # Track obstacles across frames
        tracked_map = self._tracker.update(raw_centroids)

        obstacles = []
        centroid_to_idx = {}
        for i, c in enumerate(raw_centroids):
            centroid_to_idx[c] = i

        for tid, (cx, cy) in tracked_map.items():
            idx = centroid_to_idx.get((cx, cy))
            if idx is None:
                continue
            bbox, area, _, _ = raw_data[idx]
            
            # Enhanced distance estimation
            dist = self._pixel_to_distance(cy, h)
            zone = self._classify_zone(dist)
            lane = self._classify_lane(cx, w)
            vel = self._tracker.get_velocity(tid)
            seen = self._tracker.get_frames_seen(tid)
            
            # Calculate time-to-collision for threat assessment
            ttc = self._calculate_ttc(dist, vel)
            
            # Multi-zone threat assessment
            threat_level = self._assess_threat_level(dist, ttc, vel)

            obstacles.append(TrackedObstacle(
                id=tid,
                bbox=bbox,
                area=area,
                centroid=(cx, cy),
                zone=zone,
                lane=lane,
                distance_m=dist,
                frames_seen=seen,
                velocity_px=vel,
                threat_level=threat_level,
                ttc=ttc
            ))
        
        self._frame_count += 1
        return obstacles

    # ═══════════════════════════════════════════════════════════════
    # LANE ANALYSIS
    # ═══════════════════════════════════════════════════════════════
    def analyze_lanes(self, obstacles: list[TrackedObstacle]) -> LaneStatus:
        stable = [o for o in obstacles if o.frames_seen >= 3]

        lane_nearest = {"LEFT": 99.0, "CENTER": 99.0, "RIGHT": 99.0}
        for obs in stable:
            if obs.distance_m < lane_nearest[obs.lane]:
                lane_nearest[obs.lane] = obs.distance_m

        def status(dist):
            if dist <= ZONE_NEAR_M:
                return "blocked"
            elif dist <= ZONE_MID_M:
                return "caution"
            return "clear"

        ls = LaneStatus(
            left=status(lane_nearest["LEFT"]),
            center=status(lane_nearest["CENTER"]),
            right=status(lane_nearest["RIGHT"]),
            nearest_obstacle_m=min(lane_nearest.values())
        )

        if ls.center == "clear":
            ls.free_path = "center"
        elif ls.left == "clear" and ls.right == "clear":
            ls.free_path = "left" if lane_nearest["LEFT"] > lane_nearest["RIGHT"] else "right"
        elif ls.left == "clear":
            ls.free_path = "left"
        elif ls.right == "clear":
            ls.free_path = "right"
        elif ls.center == "caution":
            ls.free_path = "center"
        elif ls.left == "caution":
            ls.free_path = "left"
        elif ls.right == "caution":
            ls.free_path = "right"
        else:
            ls.free_path = "none"

        return ls

    # ═══════════════════════════════════════════════════════════════
    # REVERSE-CAMERA GRID OVERLAY
    # ═══════════════════════════════════════════════════════════════
    def draw_reverse_camera_grid(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        vanish_y = int(h * VANISH_Y_FRAC)
        vanish_x = w // 2

        near_y = self._distance_to_pixel_y(ZONE_NEAR_M, h)
        mid_y = self._distance_to_pixel_y(ZONE_MID_M, h)

        # Zone fills (barely visible - just a hint)
        overlay = frame.copy()
        pts_near = np.array([[0, h], [w, h], [w, near_y], [0, near_y]], np.int32)
        cv2.fillPoly(overlay, [pts_near], (0, 0, 15))
        pts_mid = np.array([[0, near_y], [w, near_y], [w, mid_y], [0, mid_y]], np.int32)
        cv2.fillPoly(overlay, [pts_mid], (0, 8, 15))
        frame = cv2.addWeighted(overlay, 0.03, frame, 0.97, 0)

        # Distance arcs (subtle curved markers)
        for dist in [0.5, 1.0, 2.0, 3.0]:
            y = self._distance_to_pixel_y(dist, h)
            if y < vanish_y or y > h:
                continue
            progress = (h - y) / (h - vanish_y)
            margin = int(progress * w * 0.45)
            color = self.RED if dist <= ZONE_NEAR_M else (
                self.YELLOW if dist <= ZONE_MID_M else self.GREEN)
            alpha = max(0.15, 0.5 - progress * 0.3)
            dim_color = tuple(int(c * alpha) for c in color)
            radius = (w - 2 * margin) // 2
            if radius > 20:
                cv2.ellipse(frame, (vanish_x, y + radius // 6),
                            (radius, radius // 6), 0, 180, 360,
                            dim_color, 1, cv2.LINE_AA)
            cv2.putText(frame, f"{dist:.0f}m", (margin + 5, y - 3),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.25, dim_color, 1, cv2.LINE_AA)

        # Zone labels
        label_alpha = 0.6
        danger_color = tuple(int(c * label_alpha) for c in self.RED)
        caution_color = tuple(int(c * label_alpha) for c in self.YELLOW)
        safe_color = tuple(int(c * label_alpha) for c in self.GREEN)
        cv2.putText(frame, "DANGER", (w - 70, near_y + 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.3, danger_color, 1, cv2.LINE_AA)
        cv2.putText(frame, "CAUTION", (w - 75, mid_y + 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.3, caution_color, 1, cv2.LINE_AA)
        cv2.putText(frame, "SAFE", (w - 50, mid_y - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.3, safe_color, 1, cv2.LINE_AA)

        return frame

    def draw_planned_path(
        self,
        frame: np.ndarray,
        trajectory_pixels: list[tuple[int, int]],
        confidence: float = 0.5,
        feasible_count: int = 0,
    ) -> np.ndarray:
        """Draw the planner's chosen trajectory arc on the camera frame.

        Parameters
        ----------
        trajectory_pixels : list of (px, py) from PlannerOutput
        confidence : planner confidence (affects colour)
        feasible_count : number of feasible paths (0 = stopped)
        """
        h, w = frame.shape[:2]

        if not trajectory_pixels or len(trajectory_pixels) < 2:
            # No path — draw stop indicator
            if feasible_count == 0:
                cx, cy = w // 2, h - 50
                cv2.putText(frame, "BLOCKED", (cx - 45, cy),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, self.RED, 2, cv2.LINE_AA)
                # Draw X marker
                sz = 18
                cv2.line(frame, (cx - sz, cy + 10 - sz), (cx + sz, cy + 10 + sz),
                         self.RED, 3, cv2.LINE_AA)
                cv2.line(frame, (cx - sz, cy + 10 + sz), (cx + sz, cy + 10 - sz),
                         self.RED, 3, cv2.LINE_AA)
            return frame

        pts = np.array(trajectory_pixels, dtype=np.int32)

        # Choose colour based on confidence: green (good) → yellow → red (bad)
        if confidence > 0.6:
            path_color = self.GREEN
        elif confidence > 0.3:
            path_color = self.YELLOW
        else:
            path_color = self.RED

        # Draw glowing path: outer glow + inner bright line
        # Outer glow (wider, dimmer)
        glow_color = tuple(int(c * 0.3) for c in path_color)
        cv2.polylines(frame, [pts], isClosed=False, color=glow_color,
                      thickness=12, lineType=cv2.LINE_AA)
        # Mid layer
        mid_color = tuple(int(c * 0.6) for c in path_color)
        cv2.polylines(frame, [pts], isClosed=False, color=mid_color,
                      thickness=6, lineType=cv2.LINE_AA)
        # Inner bright line
        cv2.polylines(frame, [pts], isClosed=False, color=path_color,
                      thickness=2, lineType=cv2.LINE_AA)

        # Arrow head at the end of the trajectory
        if len(pts) >= 2:
            tip = tuple(pts[-1])
            prev = tuple(pts[-2])
            cv2.arrowedLine(frame, prev, tip, path_color, 3,
                            cv2.LINE_AA, tipLength=0.5)

        # Small waypoint dots along the path
        step = max(1, len(pts) // 5)
        for i in range(step, len(pts), step):
            cv2.circle(frame, tuple(pts[i]), 3, path_color, -1, cv2.LINE_AA)

        return frame

    # ═══════════════════════════════════════════════════════════════
    # DRAW OBSTACLES
    # ═══════════════════════════════════════════════════════════════
    def draw_obstacles(self, frame: np.ndarray, obstacles: list[TrackedObstacle],
                       lane_status: LaneStatus) -> np.ndarray:
        h, w = frame.shape[:2]

        for obs in obstacles:
            if obs.frames_seen < 3:
                continue
            color = self._zone_color(obs.zone)
            b = obs.bbox
            cv2.rectangle(frame, (b.x, b.y), (b.x + b.w, b.y + b.h), color, 2)
            cv2.putText(frame, f"{obs.distance_m:.1f}m", (b.x, b.y - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
            cv2.putText(frame, f"#{obs.id}", (b.x + b.w - 25, b.y - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.3, self.WHITE, 1, cv2.LINE_AA)
            cx, cy = obs.centroid
            cv2.circle(frame, (cx, cy), 4, color, -1)
            vx, vy = obs.velocity_px
            if abs(vx) > 1 or abs(vy) > 1:
                cv2.arrowedLine(frame, (cx, cy),
                                (int(cx + vx * 5), int(cy + vy * 5)),
                                self.CYAN, 2, cv2.LINE_AA)

        # Planned path is drawn separately via draw_planned_path()
        return frame

    # ═══════════════════════════════════════════════════════════════
    # NO SIGNAL
    # ═══════════════════════════════════════════════════════════════
    def draw_no_signal(self, width: int = 640, height: int = 480) -> np.ndarray:
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:] = (10, 14, 23)
        noise = np.random.randint(0, 30, (height, width), dtype=np.uint8)
        for c in range(3):
            frame[:, :, c] = cv2.add(frame[:, :, c], noise)
        font = cv2.FONT_HERSHEY_SIMPLEX
        text = "NO SIGNAL"
        (tw, th), _ = cv2.getTextSize(text, font, 1.5, 3)
        cx, cy = (width - tw) // 2, (height + th) // 2
        cv2.putText(frame, text, (cx, cy), font, 1.5, (0, 50, 255), 3, cv2.LINE_AA)
        sub = "Reconnecting to ESP32..."
        (sw, _), _ = cv2.getTextSize(sub, font, 0.5, 1)
        cv2.putText(frame, sub, ((width - sw) // 2, cy + 40), font, 0.5,
                    (100, 100, 150), 1, cv2.LINE_AA)
        return frame

    # ═══════════════════════════════════════════════════════════════
    # MAIN ENTRY
    # ═══════════════════════════════════════════════════════════════
    def process_frame(self, frame: np.ndarray) -> tuple[
        np.ndarray, list[TrackedObstacle], LaneStatus,
        np.ndarray | None, np.ndarray | None,
    ]:
        """Process a frame and return (annotated, obstacles, lane_status, edge_map, floor_mask)."""
        self._frame_count += 1
        obstacles = self.detect_obstacles(frame)
        lane_status = self.analyze_lanes(obstacles)
        annotated = frame.copy()
        annotated = self.draw_reverse_camera_grid(annotated)
        annotated = self.draw_obstacles(annotated, obstacles, lane_status)
        return annotated, obstacles, lane_status, self._latest_edge_map, self._latest_floor_mask
