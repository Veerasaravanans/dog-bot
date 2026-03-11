import json
import logging
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.models.schemas import MotorDirection, ControlMode, AIDecision, Detection

logger = logging.getLogger("dogbot.ws.control")

router = APIRouter()

# Services injected at startup
esp32_client = None
ai_engine = None
frame_manager = None

# Connected control clients
_control_clients: list[WebSocket] = []


def set_services(esp32, ai, fm):
    global esp32_client, ai_engine, frame_manager
    esp32_client = esp32
    ai_engine = ai
    frame_manager = fm


async def broadcast_control(message: dict):
    """Broadcast a message to all connected control clients."""
    dead = []
    data = json.dumps(message)
    for ws in _control_clients:
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _control_clients.remove(ws)


async def on_ai_decision(decision: AIDecision):
    """Callback when AI makes a decision — forward to ESP32 and broadcast."""
    if esp32_client:
        await esp32_client.send_motor_command(decision.action.value)

    await broadcast_control({
        "type": "ai_decision",
        "action": decision.action.value,
        "reasoning": decision.reasoning,
        "confidence": decision.confidence,
        "timestamp": decision.timestamp,
        "overridden": decision.overridden,
        "override_reason": decision.override_reason
    })


async def on_detection_alert(detection: Detection):
    """Callback for high-confidence detection alerts."""
    await broadcast_control({
        "type": "alert",
        "class_name": detection.class_name,
        "confidence": detection.confidence,
        "zone": detection.zone,
        "bbox": detection.bbox.model_dump()
    })


@router.websocket("/ws/control")
async def control_websocket(ws: WebSocket):
    await ws.accept()
    _control_clients.append(ws)
    logger.info(f"Control WebSocket connected (total: {len(_control_clients)})")

    # Send current status
    await ws.send_text(json.dumps({
        "type": "status",
        "mode": ai_engine.mode.value if ai_engine else "manual",
        "connected": esp32_client.connected if esp32_client else False
    }))

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type", "")

            if msg_type == "motor":
                direction = msg.get("direction", "stop")
                speed = msg.get("speed", 200)
                if isinstance(speed, int):
                    speed = max(50, min(255, speed))
                else:
                    speed = 200
                try:
                    dir_enum = MotorDirection(direction)
                except ValueError:
                    await ws.send_text(json.dumps({"type": "error", "msg": "Invalid direction"}))
                    continue

                # Register manual input (pauses AI)
                if ai_engine:
                    ai_engine.register_manual_input()
                    ai_engine.set_motor_state(dir_enum)

                # Send to ESP32
                success = False
                if esp32_client:
                    success = await esp32_client.send_motor_command(direction, speed)

                await broadcast_control({
                    "type": "motor_ack",
                    "direction": direction,
                    "speed": speed,
                    "success": success,
                    "source": "manual"
                })

            elif msg_type == "mode":
                mode_val = msg.get("value", "manual")
                try:
                    mode = ControlMode(mode_val)
                    if ai_engine:
                        ai_engine.set_mode(mode)
                    await broadcast_control({
                        "type": "mode_change",
                        "mode": mode.value
                    })
                except ValueError:
                    await ws.send_text(json.dumps({"type": "error", "msg": "Invalid mode"}))

    except WebSocketDisconnect:
        logger.info("Control WebSocket disconnected")
    except Exception as e:
        logger.error(f"Control WebSocket error: {e}")
    finally:
        if ws in _control_clients:
            _control_clients.remove(ws)
