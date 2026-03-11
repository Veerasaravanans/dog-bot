import logging
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from backend.models.schemas import ControlMode

logger = logging.getLogger("dogbot.api")

router = APIRouter(prefix="/api")

# Services injected at startup
esp32_client = None
ai_engine = None
frame_manager = None


def set_services(esp32, ai, fm):
    global esp32_client, ai_engine, frame_manager
    esp32_client = esp32
    ai_engine = ai
    frame_manager = fm


@router.get("/health")
async def health():
    esp32_status = None
    if esp32_client:
        esp32_status = await esp32_client.get_status()

    return {
        "status": "ok",
        "esp32_connected": esp32_client.connected if esp32_client else False,
        "esp32": esp32_status,
        "mode": ai_engine.mode.value if ai_engine else "manual"
    }


@router.get("/telemetry")
async def telemetry():
    if frame_manager:
        return frame_manager.telemetry.model_dump()
    return {"error": "Frame manager not initialized"}


@router.post("/mode")
async def set_mode(mode: str):
    try:
        mode_enum = ControlMode(mode)
        if ai_engine:
            ai_engine.set_mode(mode_enum)
        return {"status": "ok", "mode": mode_enum.value}
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid mode: {mode}. Use 'manual' or 'semi_auto'"}
        )


@router.get("/decisions")
async def get_decisions():
    if ai_engine:
        return [d.model_dump() for d in ai_engine.decision_log]
    return []


@router.get("/detections")
async def get_detections():
    if frame_manager:
        return [d.model_dump() for d in frame_manager.detection_history]
    return []
