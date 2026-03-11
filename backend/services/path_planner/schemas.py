"""Data schemas for the path planning pipeline."""

from dataclasses import dataclass, field
import numpy as np


@dataclass
class GridConfig:
    """Bird's-eye occupancy grid dimensions."""
    width_m: float = 3.0        # grid width in metres
    depth_m: float = 4.0        # grid depth in metres
    cell_size_m: float = 0.05   # cell resolution (5 cm)

    @property
    def cols(self) -> int:
        return int(self.width_m / self.cell_size_m)   # 60

    @property
    def rows(self) -> int:
        return int(self.depth_m / self.cell_size_m)    # 80

    @property
    def shape(self) -> tuple[int, int]:
        return (self.rows, self.cols)


@dataclass
class ObstacleState:
    """Kalman-filtered obstacle state in world coordinates."""
    track_id: int
    x: float            # metres, lateral (0 = centre)
    y: float            # metres, forward distance
    vx: float = 0.0     # lateral velocity m/s
    vy: float = 0.0     # forward velocity m/s
    radius: float = 0.1 # estimated obstacle radius in metres
    confidence: float = 0.5
    class_name: str = ""
    frames_unseen: int = 0


@dataclass
class CandidateTrajectory:
    """A single evaluated trajectory arc."""
    steering: float     # [-1, 1]
    speed: float        # [0, 1]
    obstacle_cost: float = 0.0
    heading_cost: float = 0.0
    smoothness_cost: float = 0.0
    clearance_cost: float = 0.0
    progress_cost: float = 0.0
    total_cost: float = 0.0
    feasible: bool = True


@dataclass
class PlannerOutput:
    """Final output of the path planning pipeline."""
    steering: float = 0.0      # [-1, 1] continuous
    speed: float = 0.0         # [0, 1] continuous
    confidence: float = 0.0
    reasoning: str = ""
    latency_ms: float = 0.0
    feasible_count: int = 0
    nearest_obstacle_m: float = 99.0
    # Pixel-space points for drawing the chosen trajectory on the camera frame.
    # List of (px, py) tuples in image coordinates.  Empty when stopped.
    trajectory_pixels: list[tuple[int, int]] = field(default_factory=list)
