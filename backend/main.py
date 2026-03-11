import logging
import sys
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.config import settings
from backend.services.esp32_client import ESP32Client
from backend.services.mqtt_bridge import MQTTBridge
from backend.services.cv_pipeline import CVPipeline
from backend.services.ml_detector import MLDetector
from backend.services.ai_decision import AIDecisionEngine
from backend.services.frame_manager import FrameManager

from backend.routers import video_ws, control_ws, api

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("dogbot")

# Service instances
mqtt_bridge = MQTTBridge()
esp32 = ESP32Client(mqtt_bridge=mqtt_bridge)
cv_pipeline = CVPipeline()
ml_detector = MLDetector()
ai_engine = AIDecisionEngine()
frame_mgr = FrameManager(esp32, cv_pipeline, ml_detector, ai_engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("=== DogBot Recon System Starting ===")

    # Wire up services to routers
    video_ws.set_frame_manager(frame_mgr)
    control_ws.set_services(esp32, ai_engine, frame_mgr)
    api.set_services(esp32, ai_engine, frame_mgr)

    # Register callbacks
    ai_engine.set_on_decision(control_ws.on_ai_decision)
    frame_mgr.on_alert(control_ws.on_detection_alert)

    # Start services
    await mqtt_bridge.start()
    await esp32.start()
    await ml_detector.start()
    await ai_engine.start()
    await frame_mgr.start()

    logger.info("=== All Systems Online ===")
    logger.info(f"Dashboard: http://{settings.app_host}:{settings.app_port}")
    logger.info(f"ESP32 Stream: {settings.esp32_stream_url}")

    yield

    # Shutdown
    logger.info("=== Shutting Down ===")
    await frame_mgr.stop()
    await ai_engine.stop()
    await esp32.stop()
    await mqtt_bridge.stop()
    logger.info("=== Shutdown Complete ===")


app = FastAPI(
    title="DogBot Recon System",
    version="1.0.0",
    lifespan=lifespan
)

# Include routers
app.include_router(video_ws.router)
app.include_router(control_ws.router)
app.include_router(api.router)

# Mount static files
app.mount("/static", StaticFiles(directory="backend/static"), name="static")


@app.get("/")
async def serve_dashboard():
    return FileResponse("backend/static/index.html")


@app.get("/pin-diagram")
async def serve_pin_diagram():
    return FileResponse("backend/static/pin_diagram.html")


@app.get("/setup-guide")
async def serve_setup_guide():
    return FileResponse("backend/static/setup_guide.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False
    )
