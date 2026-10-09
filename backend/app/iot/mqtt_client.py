import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import paho.mqtt.client as mqtt

from app.core.config import settings
from app.core.logging import get_logger
from app.iot import topics

logger = get_logger(__name__)

MessageHandler = Callable[[str, dict], Awaitable[None]]


class MqttBridge:
    """paho-mqtt 与 asyncio 的桥接层。

    paho 的回调运行在自己的网络线程, 这里统一用 run_coroutine_threadsafe
    把消息投递回主事件循环, 业务代码可以安全地使用异步数据库会话。
    Broker 不可用时不阻断启动, 平台仍可通过 REST 接口工作。
    """

    def __init__(self) -> None:
        self._client: mqtt.Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._handler: MessageHandler | None = None
        self.connected = False
        self.last_error = ""
        self.published = 0
        self.received = 0

    def set_handler(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"{settings.mqtt_client_id}",
            protocol=mqtt.MQTTv311,
        )
        if settings.mqtt_username:
            client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client = client
        try:
            client.connect_async(settings.mqtt_host, settings.mqtt_port, keepalive=45)
            client.loop_start()
            logger.info(
                "MQTT 客户端已启动, 目标 %s:%s", settings.mqtt_host, settings.mqtt_port
            )
        except Exception as exc:
            self.last_error = str(exc)
            logger.warning("MQTT 连接失败(%s), 平台将以纯 REST 模式运行", exc)

    async def stop(self) -> None:
        client, self._client = self._client, None
        self.connected = False
        if client is None:
            return

        def _shutdown() -> None:
            try:
                client.disconnect()
            except Exception:
                pass
            # loop_stop 会 join 网络线程, 放到线程池里避免阻塞事件循环
            client.loop_stop()

        await asyncio.to_thread(_shutdown)

    def _on_connect(self, client: mqtt.Client, userdata, flags, reason_code, properties=None):  # noqa: ANN001
        if getattr(reason_code, "is_failure", False):
            self.connected = False
            self.last_error = str(reason_code)
            logger.warning("MQTT 连接被拒绝: %s", reason_code)
            return
        self.connected = True
        self.last_error = ""
        for topic in topics.SUBSCRIPTIONS:
            client.subscribe(topic, qos=1)
        logger.info("MQTT 已连接, 订阅 %d 个主题", len(topics.SUBSCRIPTIONS))

    def _on_disconnect(self, client: mqtt.Client, userdata, flags, reason_code, properties=None):  # noqa: ANN001
        self.connected = False
        logger.warning("MQTT 连接断开: %s", reason_code)

    def _on_message(self, client: mqtt.Client, userdata, msg: mqtt.MQTTMessage):  # noqa: ANN001
        self.received += 1
        if not self._handler or not self._loop:
            return
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.warning("丢弃非法 MQTT 报文, 主题=%s", msg.topic)
            return
        asyncio.run_coroutine_threadsafe(
            self._safe_handle(msg.topic, payload), self._loop
        )

    async def _safe_handle(self, topic: str, payload: dict) -> None:
        try:
            await self._handler(topic, payload)  # type: ignore[misc]
        except Exception:
            logger.exception("处理 MQTT 消息失败, 主题=%s", topic)

    def publish(self, topic: str, payload: dict[str, Any], qos: int = 1) -> bool:
        if not self._client:
            return False
        data = json.dumps(payload, ensure_ascii=False, default=str)
        result = self._client.publish(topic, data, qos=qos)
        ok = result.rc == mqtt.MQTT_ERR_SUCCESS
        if ok:
            self.published += 1
        else:
            logger.warning("MQTT 发布失败 rc=%s topic=%s", result.rc, topic)
        return ok

    def status(self) -> dict:
        return {
            "connected": self.connected,
            "host": settings.mqtt_host,
            "port": settings.mqtt_port,
            "published": self.published,
            "received": self.received,
            "last_error": self.last_error,
        }


mqtt_bridge = MqttBridge()
