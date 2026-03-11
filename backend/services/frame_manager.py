import asyncio
import base64
import logging
import time
from datetime import datetime

import cv2
import numpy as np

from backend.models.schemas import (
    FrameResult, SystemTelemetry, ControlMode, MotorDirection, Detection, AIDecision
)
from backend.services.esp32_client import ESP32Client
from backend.services.cv_pipeline import CVPipeline
from backend.services.ml_detector import MLDetector
from backend.services.ai_decision import AIDecisionEngine

logger = logging.getLogger("dogbot.frame")


class FrameManager:
    def __init__(
        self,
        esp32: ESP32Client,
        cv_pipeline: CVPipeline,
        ml_detector: MLDetector,
        ai_engine: AIDecisionEngine,
    ):
        self._esp32 = esp32
        self._cv = cv_pipeline
        self._ml = ml_detector
        self._ai = ai_engine
        self._running = False
        self._process_task: asyncio.Task | None = None
        self._subscribers: dict[int, asyncio.Queue] = {}
        self._sub_counter = 0
        self._telemetry = SystemTelemetry()
        self._fps_counter = 0
        self._fps_time = time.time()
        self._detection_history: list[Detection] = []
        self._alert_callbacks: list = []

    @property
    def telemetry(self) -> SystemTelemetry:
        return self._telemetry

    @property
    def detection_history(self) -> list[Detection]:
        return self._detection_history[-100:]

    def subscribe(self) -> tuple[int, asyncio.Queue]:
        self._sub_counter += 1
        queue = asyncio.Queue(maxsize=2)
        self._subscribers[self._sub_counter] = queue
        logger.info(f"Subscriber added: {self._sub_counter} (total: {len(self._subscribers)})")
        return self._sub_counter, queue

    def unsubscribe(self, sub_id: int):
        self._subscribers.pop(sub_id, None)
        logger.info(f"Subscriber removed: {sub_id} (total: {len(self._subscribers)})")

    def on_alert(self, callback):
        self._alert_callbacks.append(callback)

    async def start(self):
        self._running = True
        self._process_task = asyncio.create_task(self._processing_loop())
        logger.info("Frame manager started")

    async def stop(self):
        self._running = False
        if self._process_task:
            self._process_task.cancel()
            try:
                await self._process_task
            except asyncio.CancelledError:
                pass

    async def _processing_loop(self):
        target_interval = 1.0 / 15  # 15 FPS target

        while self._running:
            loop_start = time.time()

            try:
                raw_frame = await self._esp32.get_latest_frame()

                if raw_frame is None:
                    # No signal frame
                    no_signal = self._cv.draw_no_signal()
                    _, jpeg = cv2.imencode('.jpg', no_signal, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    frame_b64 = base64.b64encode(jpeg.tobytes()).decode()

                    result = FrameResult(
                        timestamp=datetime.now().isoformat(),
                        frame_b64=frame_b64,
                        telemetry=self._telemetry
                    )
                    self._telemetry.connected = False
                    await self._broadcast(result)
                    await asyncio.sleep(0.5)
                    continue

                self._telemetry.connected = True
                self._telemetry.source = self._esp32.source

                # CV processing on clean frame (returns lane_status + auxiliary maps)
                annotated, obstacles, lane_status, edge_map, floor_mask = self._cv.process_frame(raw_frame)

                # ML detection on clean frame
                detections = await self._ml.detect(raw_frame)

                # Draw ML detections on annotated frame
                annotated = self._ml.draw_detections(annotated, detections)

                # Update AI engine with scene data including lane analysis
                self._ai.update_scene(obstacles, detections, lane_status)

                # Run planner every frame (~2ms) so trajectory is always fresh
                planner_out = self._ai.path_planner.plan(
                    obstacles, detections, edge_map, floor_mask
                )
                self._telemetry.planner_latency_ms = planner_out.latency_ms
                self._telemetry.planner_feasible_paths = planner_out.feasible_count
                self._telemetry.nearest_obstacle_m = planner_out.nearest_obstacle_m

                # Draw the planner's chosen trajectory on the camera feed
                annotated = self._cv.draw_planned_path(
                    annotated,
                    planner_out.trajectory_pixels,
                    confidence=planner_out.confidence,
                    feasible_count=planner_out.feasible_count,
                )

                # Check for high-confidence detections → alerts
                for det in detections:
                    if det.confidence > 0.6:
                        self._detection_history.append(det)
                        for cb in self._alert_callbacks:
                            asyncio.create_task(cb(det))

                # Draw FPS + timestamp
                self._fps_counter += 1
                elapsed = time.time() - self._fps_time
                if elapsed >= 1.0:
                    self._telemetry.fps = round(self._fps_counter / elapsed, 1)
                    self._fps_counter = 0
                    self._fps_time = time.time()

                cv2.putText(annotated, f"FPS: {self._telemetry.fps}",
                            (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                            (0, 255, 136), 1, cv2.LINE_AA)
                cv2.putText(annotated, datetime.now().strftime("%H:%M:%S"),
                            (540, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                            (200, 200, 200), 1, cv2.LINE_AA)

                # Encode for transmission
                _, jpeg = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])
                frame_b64 = base64.b64encode(jpeg.tobytes()).decode()

                # Update telemetry
                self._telemetry.esp32_rssi = self._esp32.rssi
                self._telemetry.detection_count = len(detections)
                self._telemetry.obstacle_count = len([o for o in obstacles if o.frames_seen >= 2])
                self._telemetry.mode = self._ai.mode
                self._telemetry.motor_state = self._ai._motor_state
                self._telemetry.lane_status = lane_status

                result = FrameResult(
                    timestamp=datetime.now().isoformat(),
                    detections=detections,
                    obstacles=obstacles,
                    lane_status=lane_status,
                    telemetry=self._telemetry,
                    frame_b64=frame_b64
                )

                await self._broadcast(result)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Frame processing error: {e}", exc_info=True)
                # Send error frame to prevent black screen
                try:
                    error_frame = np.zeros((480, 640, 3), dtype=np.uint8)
                    cv2.putText(error_frame, f"ERROR: {str(e)[:50]}",
                                (20, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                                (0, 0, 255), 2, cv2.LINE_AA)
                    _, jpeg = cv2.imencode('.jpg', error_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    frame_b64 = base64.b64encode(jpeg.tobytes()).decode()
                    result = FrameResult(
                        timestamp=datetime.now().isoformat(),
                        frame_b64=frame_b64,
                        telemetry=self._telemetry
                    )
                    await self._broadcast(result)
                except:
                    pass
                await asyncio.sleep(0.5)

            # Maintain target FPS
            processing_time = time.time() - loop_start
            sleep_time = max(0, target_interval - processing_time)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    async def _broadcast(self, result: FrameResult):
        """Send frame result to all subscribers with backpressure."""
        data = result.model_dump_json()
        dead = []
        for sub_id, queue in self._subscribers.items():
            try:
                if queue.full():
                    try:
                        queue.get_nowait()  # Drop oldest
                    except asyncio.QueueEmpty:
                        pass
                queue.put_nowait(data)
            except Exception:
                dead.append(sub_id)

        for sub_id in dead:
            self._subscribers.pop(sub_id, None)
