"""DogBot Routers — WebSocket and REST API endpoints."""

from backend.routers import video_ws, control_ws, api

__all__ = ["video_ws", "control_ws", "api"]
