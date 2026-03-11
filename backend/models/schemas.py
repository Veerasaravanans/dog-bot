from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class MotorDirection(str, Enum):
    FORWARD = "forward"
    BACK = "back"
    LEFT = "left"
    RIGHT = "right"
    STOP = "stop"


class ControlMode(str, Enum):
    MANUAL = "manual"
    SEMI_AUTO = "semi_auto"
    SEARCH = "search"


class MotorCommand(BaseModel):
    direction: MotorDirection
    source: str = "manual"  # "manual" or "ai"


class BBox(BaseModel):
    x: int
    y: int
    w: int
    h: int


class Detection(BaseModel):
    class_name: str
    confidence: float
    bbox: BBox
    zone: str = "unknown"
    track_id: int | None = None
    mask: list[list[int]] | None = None  # Polygon points for segmentation


class TrackedObstacle(BaseModel):
    """Obstacle with tracking, distance estimation, and lane assignment."""
    id: int
    bbox: BBox
    area: float
    centroid: tuple[int, int]
    zone: str            # DANGER / CAUTION / SAFE
    lane: str            # LEFT / CENTER / RIGHT
    distance_m: float    # estimated distance in metres
    frames_seen: int = 1
    velocity_px: tuple[float, float] = (0.0, 0.0)
    threat_level: int = 0  # 0=safe, 1=low, 2=medium, 3=high, 4=critical
    ttc: float = 999.0     # time-to-collision in seconds


class LaneStatus(BaseModel):
    """Free-path analysis for each lane."""
    left: str = "clear"       # clear / caution / blocked
    center: str = "clear"
    right: str = "clear"
    free_path: str = "center" # recommended path: left / center / right / none
    nearest_obstacle_m: float = 99.0


class AIDecision(BaseModel):
    action: MotorDirection
    reasoning: str
    confidence: float
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    overridden: bool = False
    override_reason: Optional[str] = None


class SystemTelemetry(BaseModel):
    fps: float = 0.0
    esp32_rssi: int = 0
    ai_latency_ms: float = 0.0
    detection_count: int = 0
    obstacle_count: int = 0
    mode: ControlMode = ControlMode.MANUAL
    motor_state: MotorDirection = MotorDirection.STOP
    connected: bool = False
    source: str = "none"
    lane_status: Optional[LaneStatus] = None
    planner_latency_ms: float = 0.0
    planner_feasible_paths: int = 0
    nearest_obstacle_m: float = 99.0


class FrameResult(BaseModel):
    timestamp: str
    detections: list[Detection] = []
    obstacles: list[TrackedObstacle] = []
    lane_status: Optional[LaneStatus] = None
    telemetry: SystemTelemetry = SystemTelemetry()
    frame_b64: str = ""
