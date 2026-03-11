# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DogBot Recon System: An autonomous reconnaissance robot using ESP32-CAM for vision, computer vision for obstacle detection, path planning for navigation, and AI-powered decision-making via VIO Cloud LLM integration.

**Key Technologies:** FastAPI backend, ESP32-CAM firmware (Arduino), OpenCV/YOLOv8 for vision, real-time path planning, MQTT for remote connectivity, WebSocket streaming.

## Common Commands

### Running the Application

**IMPORTANT for Windows users:** Use `run.py` to ensure proper MQTT compatibility.

```bash
# Recommended: Start using the startup script (works on all platforms)
python run.py

# Alternative (Windows): Run main.py directly
python backend/main.py

# NOT RECOMMENDED on Windows: Direct uvicorn (event loop policy issues)
# python -m uvicorn backend.main:app  # ❌ MQTT won't work on Windows
```

**Why use run.py?**
- Sets Windows event loop policy **before** uvicorn starts
- Ensures MQTT/aiomqtt compatibility on Windows
- Uses configuration from .env file automatically
- Works identically on Linux/Mac/Windows

### Development Environment

```bash
# Install dependencies
pip install -r requirements.txt

# Virtual environment is in .venv/ or venv/
# Activate: .venv\Scripts\activate (Windows) or source .venv/bin/activate (Linux/Mac)
```

### ESP32 Firmware

- **Location:** `firmware/esp32cam_dogbot/esp32cam_dogbot.ino`
- **Board:** AI-Thinker ESP32-CAM
- **Upload:** Use Arduino IDE with ESP32 board support
- **Configuration:** WiFi credentials and MQTT settings are hardcoded in the .ino file (lines 14-26)

### Remote Access Setup

**For accessing ESP32-CAM from different locations (e.g., Chennai to another city):**

See **`REMOTE_ACCESS_GUIDE.md`** for complete instructions.

**Quick Summary:**
- ✅ **MQTT Control:** Already works remotely (motor commands via HiveMQ Cloud)
- ❌ **Video Stream:** Currently local network only (`http://192.168.1.100:81/stream`)
- 🔧 **Solution:** Use VPN (Tailscale recommended) or cloud relay (ngrok for testing)

**Recommended for Remote Video Access:**
1. **Tailscale VPN** (5 min setup, free, secure) - Install on both computers, access ESP32 as if local
2. **ngrok** (2 min setup, free tier) - Temporary public URL for testing
3. **Webcam Fallback** (works now) - System automatically uses local webcam when ESP32 unreachable

Update `.env` file with remote URL after setting up access method.

### Testing/Debugging

- Dashboard: `http://localhost:8000` (main control interface)
- Pin Diagram: `http://localhost:8000/pin-diagram`
- Setup Guide: `http://localhost:8000/setup-guide`
- Video Stream WebSocket: `ws://localhost:8000/ws/video`
- Control WebSocket: `ws://localhost:8000/ws/control`

## Architecture Overview

### Service Layer Architecture (backend/services/)

The application uses a **service orchestration pattern** where `FrameManager` coordinates all vision and decision-making services:

1. **ESP32Client** (`esp32_client.py`)
   - Manages MJPEG stream from ESP32-CAM with automatic webcam fallback
   - Handles motor control commands via HTTP or MQTT
   - Provides dual-mode connectivity: local HTTP or remote MQTT

2. **CVPipeline** (`cv_pipeline.py`)
   - Multi-sensor fusion for obstacle detection (background subtraction, edge detection, gradient magnitude, optical flow)
   - Automotive-style reverse camera visualization with distance grid
   - Perspective-based distance estimation and lane analysis
   - Generates auxiliary maps for path planning: thin edge detection for poles/legs, adaptive floor color detection

3. **MLDetector** (`ml_detector.py`)
   - YOLOv8n-seg (segmentation model) for object detection
   - Built-in ByteTrack tracker for multi-object tracking
   - Runs every N frames for performance optimization

4. **AIDecisionEngine** (`ai_decision.py`)
   - **Primary navigation:** Uses local PathPlannerEngine (fast, no API calls)
   - **LLM fallback:** VIO Cloud LLM for low-confidence scenarios (cached, background tasks)
   - Multi-frame direction voting (5-frame window, 60% majority required)
   - Three control modes: MANUAL, SEMI_AUTO

5. **PathPlannerEngine** (`services/path_planner/engine.py`)
   - **5-stage pipeline:** Occupancy Grid → Obstacle State Estimation (Kalman) → Cost Map Generation → Trajectory Evaluation → Temporal Smoothing
   - Outputs continuous steering (-1 to +1) and speed (0 to 1)
   - Emergency stop for obstacles within 0.25m in forward corridor
   - Recovery behavior: turns toward clearer side after 15 blocked frames

6. **FrameManager** (`frame_manager.py`)
   - **Main orchestrator:** Runs at 15 FPS target
   - Coordinates: ESP32Client → CVPipeline → MLDetector → AIDecisionEngine → PathPlanner
   - Manages WebSocket subscribers with backpressure handling
   - Broadcasts `FrameResult` with annotated frame, telemetry, detections, obstacles

### Data Flow

```
ESP32-CAM (MJPEG) → ESP32Client.get_latest_frame()
                   ↓
    FrameManager._processing_loop()
                   ↓
         [Frame Processing Pipeline]
                   ↓
    CVPipeline.process_frame() → (annotated, obstacles, lane_status, edge_map, floor_mask)
                   ↓
    MLDetector.detect() → detections (YOLOv8 + tracking)
                   ↓
    AIDecisionEngine.update_scene(obstacles, detections, lane_status)
                   ↓
    PathPlannerEngine.plan() → (steering, speed, trajectory_pixels, confidence)
                   ↓
    Annotate frame with trajectory + telemetry
                   ↓
    Broadcast FrameResult to WebSocket subscribers
```

### Path Planner Sub-Architecture

Located in `backend/services/path_planner/`:

- **engine.py**: Main orchestrator, runs 5-stage pipeline
- **occupancy_grid.py**: Projects obstacles/detections into bird's-eye grid (60×80 cells, 5cm resolution)
- **obstacle_estimator.py**: Kalman filtering for obstacle velocity/position prediction
- **cost_map.py**: Generates cost field from occupancy + predicted positions
- **trajectory_evaluator.py**: Evaluates 41 candidate arcs, scores by obstacle/heading/smoothness/clearance/progress costs
- **schemas.py**: Data models (GridConfig, PlannerOutput, ObstacleState)

The planner runs **every frame** (~2ms latency) so the trajectory is always fresh.

### Configuration System

All settings are managed via Pydantic in `backend/config.py`:
- Loads from `.env` file (use `.env.example` as template)
- Key sections: ESP32 connection, VIO LLM credentials, CV parameters, ML thresholds, path planner grid dimensions
- **Security Note:** `.env` contains API tokens and should never be committed

### WebSocket Communication

- **Video Stream** (`routers/video_ws.py`): Broadcasts annotated frames with detections/telemetry at 15 FPS
- **Control Stream** (`routers/control_ws.py`): Bidirectional control (manual commands, AI decisions, mode switching)
- **AI Decision Callbacks**: AIDecisionEngine notifies control_ws via callback when new decisions are made

### ESP32 Firmware Architecture

- **Dual-server design:** Port 80 for control API, Port 81 for MJPEG stream
- **Motor control:** L293D driver with 4 GPIO pins (IN1-IN4), no PWM speed control (ENA/ENB tied HIGH)
- **MQTT integration:** TLS-enabled connection to HiveMQ Cloud for remote operation
- **Failsafe:** Motors auto-stop after 500ms of no commands

## Important Patterns

### Asyncio Service Lifecycle

All services follow this pattern:
```python
async def start(self):
    self._running = True
    self._task = asyncio.create_task(self._loop())

async def stop(self):
    self._running = False
    if self._task:
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
```

Services are started/stopped in `backend/main.py` lifespan manager in dependency order.

### Dual Connectivity (Local + Remote)

ESP32Client tries MQTT first, falls back to HTTP:
- **MQTT:** For remote operation over internet (HiveMQ Cloud broker)
- **HTTP:** For local network operation (direct ESP32 IP)
- **Stream fallback:** ESP32 → laptop webcam if ESP32 unavailable

### Frame-Skipping for Performance

- CV pipeline: Runs every frame
- ML detection: Runs every 2 frames (configurable via `ml_detect_every_n_frames`)
- Optical flow: Computed every 2 frames
- Vanishing point detection: Every 30 frames

### Multi-Frame Tracking

- **CV obstacles:** Centroid-based tracker with ID assignment, velocity estimation, age management
- **ML detections:** YOLOv8 built-in ByteTrack tracker
- **AI decisions:** 5-frame voting window (prevents single-frame noise causing wrong turns)

## Key Files to Understand

When making changes to:

- **Navigation logic:** Start with `services/ai_decision.py` and `services/path_planner/engine.py`
- **Vision processing:** `services/cv_pipeline.py` (1100+ lines, core detection algorithms)
- **Object detection:** `services/ml_detector.py` (YOLOv8 integration)
- **Service coordination:** `services/frame_manager.py` (main orchestrator)
- **API/WebSocket:** `routers/control_ws.py`, `routers/video_ws.py`, `routers/api.py`
- **Data models:** `models/schemas.py` (Pydantic models for all data structures)
- **ESP32 firmware:** `firmware/esp32cam_dogbot/esp32cam_dogbot.ino`

## Development Notes

### Adding New Features

1. **New sensor/detector:** Add service to `backend/services/`, integrate in `FrameManager._processing_loop()`
2. **New control mode:** Add to `MotorDirection` or `ControlMode` enum in `models/schemas.py`, update AI decision logic
3. **New path planner cost:** Modify `trajectory_evaluator.py` scoring weights
4. **New WebSocket event:** Add handler in `routers/control_ws.py`, update frontend

### Camera Calibration

Critical constants in `cv_pipeline.py`:
- `CAM_HEIGHT_M = 0.15` (camera height above ground)
- `VANISH_Y_FRAC = 0.30` (vanishing point Y position as fraction of frame height)
- `DIST_SCALE = 80.0` (perspective distance scaling factor)
- `ZONE_NEAR_M = 0.5`, `ZONE_MID_M = 1.2` (safety zone boundaries)

Adjust these if changing camera mounting or lens.

### Path Planner Tuning

Grid configuration in `backend/config.py`:
- `planner_grid_width_m = 3.0` (lateral coverage, ±1.5m from center)
- `planner_grid_depth_m = 4.0` (forward lookahead distance)
- `planner_cell_size_m = 0.05` (5cm grid resolution)
- `planner_emergency_stop_dist_m = 0.25` (critical safety distance)

Cost weights in `trajectory_evaluator.py`:
- `WEIGHT_OBSTACLE = 3.0` (penalty for approaching obstacles)
- `WEIGHT_HEADING = 0.8` (penalty for deviating from straight)
- `WEIGHT_SMOOTHNESS = 0.5` (penalty for changing direction)
- `WEIGHT_CLEARANCE = 2.0` (reward for maintaining safety margin)
- `WEIGHT_PROGRESS = 1.0` (reward for forward motion)

### VIO LLM Integration

The system uses a background task pattern for LLM queries:
- Queries run asynchronously, never block navigation
- Results cached for 10 seconds
- Only invoked when local planner confidence < 0.4
- Fallback response parsing handles various API formats

### Platform-Specific Notes

**Windows:**
- **MUST use `run.py`** to start the application (not `python -m uvicorn`)
- `run.py` uses `uvicorn.Server` with an explicit `SelectorEventLoop` to ensure MQTT compatibility
- This is required because Windows' default `ProactorEventLoop` doesn't support socket operations (`add_reader`/`add_writer`) needed by aiomqtt
- **Technical details:**
  - Creates `asyncio.SelectorEventLoop()` explicitly
  - Sets it as the current event loop
  - Runs `uvicorn.Server` using `loop.run_until_complete()` (not `uvicorn.run()`)
  - This ensures ALL async code (including MQTT) runs in the correct loop
- **Impact:** Slightly lower I/O performance vs ProactorEventLoop, but enables MQTT remote connectivity
- **Alternative:** Disable MQTT (set `mqtt_broker_host=""` in .env) to use HTTP-only mode

**Linux/Mac:**
- No event loop policy changes needed
- Can use `run.py`, `python backend/main.py`, or `python -m uvicorn` - all work equally
- Uses default asyncio event loop with full performance
- MQTT works out of the box

**Docker/WSL:**
- Recommended for production deployments on Windows
- Full asyncio support without event loop policy workarounds
- Better performance and compatibility

## Troubleshooting

### ESP32 won't connect
- Check WiFi credentials in firmware (lines 14-15)
- Verify ESP32 is on same network or MQTT broker is reachable
- Backend falls back to webcam if ESP32 unavailable

### MQTT TLS connection errors
- **Error: "TLSParameters.__init__() got an unexpected keyword argument"** ✅ FIXED
  - This was fixed in mqtt_bridge.py - ensure you have the latest version
  - For aiomqtt>=2.0.0, `tls_context` must be passed directly to `Client()`, not to `TLSParameters()`
  - Verify aiomqtt version: `pip show aiomqtt` should be 2.0.0 or higher

- **Windows-specific: "NotImplementedError" in add_reader/add_writer** ✅ FIXED
  - **Symptoms:** `Exception in callback AbstractEventLoop.add_reader... raise NotImplementedError`
  - **Root Cause:** Windows ProactorEventLoop doesn't support socket operations needed by aiomqtt/paho-mqtt
  - **Fix:** **MUST use `python run.py` to start the application** (not `python -m uvicorn`)
  - **How it works:** `run.py` creates a `SelectorEventLoop` explicitly and runs `uvicorn.Server` in it using `loop.run_until_complete()`. This ensures MQTT runs in the correct event loop.
  - **Verification:** Run `python run.py`, you should see:
    ```
    [INFO] Windows detected: Using SelectorEventLoop for MQTT compatibility
    [DEBUG] Event loop type: _WindowsSelectorEventLoop
    [DEBUG] Has add_reader: True
    ...
    [dogbot.mqtt] INFO: MQTT connected to broker  ✅
    ```
  - **If you still see NotImplementedError:** You're using the wrong startup method (see "Running the Application" section)
  - **Alternative:** Set `mqtt_broker_host=""` in .env to disable MQTT and use HTTP-only mode

- **MQTT keeps reconnecting / timing out:**
  - Check broker credentials in .env file (mqtt_broker_host, mqtt_username, mqtt_password)
  - Verify port 8883 is accessible (for TLS) or 1883 (for non-TLS)
  - Test broker connectivity: `telnet <broker_host> 8883` or `ping <broker_host>`
  - For HiveMQ Cloud, ensure your cluster is active and credentials are correct
  - Check firewall/antivirus isn't blocking outbound port 8883
  - If behind corporate proxy, MQTT may be blocked (use HTTP-only mode)

- **MQTT disabled:**
  - If mqtt_broker_host is empty in .env, MQTT is automatically skipped
  - System will use HTTP-only communication with ESP32 (local network only)
  - This is the recommended mode for local development without remote access

### Motors not responding
- Verify pin connections match firmware definitions (lines 52-55)
- L293D module must have power supply connected
- Check Serial Monitor in Arduino IDE for MQTT/command logs

### Poor detection performance
- Lower `ml_detect_every_n_frames` for more frequent detection (increases CPU usage)
- Adjust `ml_confidence_threshold` (default 0.5)
- Check YOLOv8 model file exists: `yolov8n-seg.pt` in project root

### Path planner always stops
- Reduce `WEIGHT_OBSTACLE` or increase `WEIGHT_PROGRESS` in trajectory_evaluator.py
- Check occupancy grid visualization (enable debug mode)
- Verify camera calibration constants are correct for your setup

### High latency
- Target is 15 FPS processing, 2ms path planning
- If slower: reduce frame resolution, skip more ML frames, or use faster YOLOv8 model
- Check CPU usage with frame_manager telemetry (fps and latency_ms)
