import asyncio
import logging
import numpy as np
import cv2
import httpx

from backend.config import settings

logger = logging.getLogger("dogbot.esp32")


class ESP32Client:
    def __init__(self, mqtt_bridge=None):
        self._mqtt_bridge = mqtt_bridge
        self._frame: np.ndarray | None = None
        self._frame_lock = asyncio.Lock()
        self._running = False
        self._connected = False
        self._esp32_connected = False
        self._webcam_active = False
        self._http_client: httpx.AsyncClient | None = None
        self._capture_task: asyncio.Task | None = None
        self._webcam_task: asyncio.Task | None = None
        self._rssi: int = 0
        self._webcam: cv2.VideoCapture | None = None
        self._source: str = "none"  # "esp32", "webcam", "none"

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def rssi(self) -> int:
        return self._rssi

    @property
    def source(self) -> str:
        return self._source

    async def start(self):
        self._http_client = httpx.AsyncClient(timeout=settings.esp32_timeout)
        self._running = True
        self._capture_task = asyncio.create_task(self._stream_capture_loop())
        logger.info("ESP32 client started (with webcam fallback)")

    async def stop(self):
        self._running = False
        if self._capture_task:
            self._capture_task.cancel()
            try:
                await self._capture_task
            except asyncio.CancelledError:
                pass
        if self._webcam_task:
            self._webcam_task.cancel()
            try:
                await self._webcam_task
            except asyncio.CancelledError:
                pass
        if self._webcam is not None:
            self._webcam.release()
            self._webcam = None
        if self._http_client:
            await self._http_client.aclose()
        logger.info("ESP32 client stopped")

    async def _stream_capture_loop(self):
        """Try ESP32 MJPEG stream first; fall back to laptop webcam."""
        while self._running:
            # Try ESP32 stream
            try:
                logger.info(f"Attempting ESP32 stream: {settings.esp32_stream_url}")
                async with httpx.AsyncClient(timeout=httpx.Timeout(
                    connect=3.0, read=10.0, write=5.0, pool=5.0
                )) as client:
                    async with client.stream("GET", settings.esp32_stream_url) as response:
                        # ESP32 connected — stop webcam if running
                        self._esp32_connected = True
                        self._connected = True
                        self._source = "esp32"
                        await self._stop_webcam()
                        logger.info("Connected to ESP32 stream")
                        buffer = b""

                        async for chunk in response.aiter_bytes(chunk_size=4096):
                            if not self._running:
                                break
                            buffer += chunk

                            while True:
                                start = buffer.find(b'\xff\xd8')
                                end = buffer.find(b'\xff\xd9')
                                if start == -1 or end == -1 or end <= start:
                                    break
                                jpeg_data = buffer[start:end + 2]
                                buffer = buffer[end + 2:]
                                frame = cv2.imdecode(
                                    np.frombuffer(jpeg_data, dtype=np.uint8),
                                    cv2.IMREAD_COLOR
                                )
                                if frame is not None:
                                    async with self._frame_lock:
                                        self._frame = frame

            except (httpx.HTTPError, httpx.StreamError, Exception) as e:
                self._esp32_connected = False
                if self._source == "esp32":
                    logger.warning(f"ESP32 stream lost: {e}")

                # Fall back to webcam
                if not self._webcam_active:
                    await self._start_webcam()

                if self._webcam_active:
                    # Wait before retrying ESP32 (webcam is providing frames)
                    await asyncio.sleep(5.0)
                else:
                    # No webcam either, short retry
                    self._connected = False
                    self._source = "none"
                    await asyncio.sleep(2.0)

    async def _start_webcam(self):
        """Start laptop webcam capture as fallback."""
        if self._webcam_active:
            return
        try:
            loop = asyncio.get_event_loop()
            self._webcam = await loop.run_in_executor(None, self._open_webcam)
            if self._webcam is not None and self._webcam.isOpened():
                self._webcam_active = True
                self._connected = True
                self._source = "webcam"
                self._webcam_task = asyncio.create_task(self._webcam_capture_loop())
                logger.info("Webcam fallback activated (laptop camera)")
            else:
                logger.warning("No webcam available for fallback")
                self._webcam = None
        except Exception as e:
            logger.warning(f"Webcam init failed: {e}")

    def _open_webcam(self) -> cv2.VideoCapture | None:
        """Open webcam (blocking, runs in executor)."""
        # Use DirectShow backend on Windows to avoid MSMF async errors
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            return cap
        cap.release()
        return None

    async def _webcam_capture_loop(self):
        """Capture frames from laptop webcam at ~20 FPS."""
        loop = asyncio.get_event_loop()
        logger.info("Webcam capture loop started")
        while self._running and self._webcam_active:
            try:
                ret, frame = await loop.run_in_executor(None, self._webcam.read)
                if ret and frame is not None:
                    async with self._frame_lock:
                        self._frame = frame
                else:
                    logger.warning("Webcam read failed")
                    break
                await asyncio.sleep(1.0 / 20)  # ~20 FPS
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Webcam capture error: {e}")
                break

        self._webcam_active = False
        logger.info("Webcam capture loop ended")

    async def _stop_webcam(self):
        """Stop webcam when ESP32 comes back online."""
        if not self._webcam_active:
            return
        self._webcam_active = False
        if self._webcam_task:
            self._webcam_task.cancel()
            try:
                await self._webcam_task
            except asyncio.CancelledError:
                pass
            self._webcam_task = None
        if self._webcam is not None:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._webcam.release)
            self._webcam = None
        logger.info("Webcam stopped (ESP32 reconnected)")

    async def get_latest_frame(self) -> np.ndarray | None:
        async with self._frame_lock:
            return self._frame.copy() if self._frame is not None else None

    async def send_motor_command(self, direction: str, speed: int = 200) -> bool:
        # Try MQTT first for remote connectivity
        if self._mqtt_bridge and self._mqtt_bridge.connected:
            try:
                result = await self._mqtt_bridge.publish_motor_command(direction, speed)
                if result:
                    return True
                logger.debug("MQTT motor publish failed, falling back to HTTP")
            except Exception as e:
                logger.debug(f"MQTT motor error: {e}, falling back to HTTP")

        # Fall back to HTTP (local network)
        if not self._http_client:
            return False
        try:
            url = f"{settings.esp32_control_url}/motor?dir={direction}&speed={speed}"
            response = await self._http_client.get(url, timeout=0.5)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Motor command failed: {e}")
            return False

    async def get_status(self) -> dict | None:
        # Try MQTT cached status first
        if self._mqtt_bridge and self._mqtt_bridge.esp32_online and self._mqtt_bridge.last_status:
            data = self._mqtt_bridge.last_status
            self._rssi = data.get("rssi", 0)
            return data

        # Fall back to HTTP (local network)
        if not self._http_client:
            return None
        try:
            url = f"{settings.esp32_control_url}/status"
            response = await self._http_client.get(url, timeout=1.0)
            if response.status_code == 200:
                data = response.json()
                self._rssi = data.get("rssi", 0)
                return data
        except Exception as e:
            logger.debug(f"Status request failed: {e}")
        return None
