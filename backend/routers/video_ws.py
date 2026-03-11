import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("dogbot.ws.video")

router = APIRouter()

# Frame manager will be injected at startup
frame_manager = None


def set_frame_manager(fm):
    global frame_manager
    frame_manager = fm


@router.websocket("/ws/video")
async def video_websocket(ws: WebSocket):
    await ws.accept()
    logger.info("Video WebSocket client connected")

    sub_id, queue = frame_manager.subscribe()

    try:
        while True:
            data = await queue.get()
            await ws.send_text(data)
    except WebSocketDisconnect:
        logger.info("Video WebSocket client disconnected")
    except Exception as e:
        logger.error(f"Video WebSocket error: {e}")
    finally:
        frame_manager.unsubscribe(sub_id)
