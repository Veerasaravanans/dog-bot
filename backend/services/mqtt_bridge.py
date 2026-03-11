import asyncio
import json
import logging
import time

from backend.config import settings

logger = logging.getLogger("dogbot.mqtt")


class MQTTBridge:
    """MQTT bridge for remote ESP32 communication via cloud broker."""

    TOPIC_CMD_MOTOR = "dogbot/cmd/motor"
    TOPIC_STATUS = "dogbot/status"
    TOPIC_HEARTBEAT = "dogbot/heartbeat"

    def __init__(self):
        self._client = None
        self._connected = False
        self._esp32_online = False
        self._last_status: dict | None = None
        self._last_heartbeat: float = 0
        self._task: asyncio.Task | None = None
        self._running = False

    @property
    def configured(self) -> bool:
        return bool(settings.mqtt_broker_host and settings.mqtt_username)

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def esp32_online(self) -> bool:
        if self._last_heartbeat and (time.time() - self._last_heartbeat > 15):
            self._esp32_online = False
        return self._esp32_online

    @property
    def last_status(self) -> dict | None:
        return self._last_status

    async def start(self):
        if not self.configured:
            logger.info("MQTT not configured, skipping")
            return
        self._running = True
        self._task = asyncio.create_task(self._connection_loop())
        logger.info(f"MQTT bridge starting -> {settings.mqtt_broker_host}:{settings.mqtt_broker_port}")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._connected = False
        logger.info("MQTT bridge stopped")

    async def publish_motor_command(self, direction: str, speed: int = 200) -> bool:
        if not self._connected or not self._client:
            return False
        try:
            await self._client.publish(
                self.TOPIC_CMD_MOTOR,
                json.dumps({"dir": direction, "speed": speed}),
                qos=1,
            )
            logger.debug(f"MQTT motor command sent: {direction} speed={speed}")
            return True
        except Exception as e:
            logger.error(f"MQTT publish failed: {e}")
            return False

    async def _connection_loop(self):
        import aiomqtt
        import ssl

        while self._running:
            try:
                # For aiomqtt 2.0+, pass tls_context directly to Client
                tls_context = None
                if settings.mqtt_use_tls:
                    tls_context = ssl.create_default_context()

                async with aiomqtt.Client(
                    hostname=settings.mqtt_broker_host,
                    port=settings.mqtt_broker_port,
                    username=settings.mqtt_username,
                    password=settings.mqtt_password,
                    tls_context=tls_context,
                ) as client:
                    self._client = client
                    self._connected = True
                    logger.info("MQTT connected to broker")

                    await client.subscribe(self.TOPIC_STATUS, qos=1)
                    await client.subscribe(self.TOPIC_HEARTBEAT, qos=0)

                    async for message in client.messages:
                        if not self._running:
                            break
                        topic = str(message.topic)
                        try:
                            payload = json.loads(message.payload.decode())
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue

                        if topic == self.TOPIC_STATUS:
                            self._last_status = payload
                            self._esp32_online = True
                            self._last_heartbeat = time.time()
                            logger.debug(f"MQTT status: {payload}")
                        elif topic == self.TOPIC_HEARTBEAT:
                            self._esp32_online = True
                            self._last_heartbeat = time.time()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._connected = False
                self._client = None
                logger.warning(f"MQTT connection lost: {e}, reconnecting in 5s")
                await asyncio.sleep(5)
