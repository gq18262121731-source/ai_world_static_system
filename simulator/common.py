"""模拟器与平台之间的通用 MQTT 客户端。

主题格式与后端 app/iot/topics.py 完全一致, 换成真机时只需要在设备端
实现同样的主题与报文即可接入。
"""

import json
import logging
import os
import random
import signal
import threading
import time
from datetime import datetime
from typing import Any, Callable

import paho.mqtt.client as mqtt

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
PREFIX = os.getenv("MQTT_TOPIC_PREFIX", "aiot")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)


def now_iso() -> str:
    return datetime.now().isoformat()


def jitter(value: float, ratio: float = 0.05) -> float:
    return value * random.uniform(1 - ratio, 1 + ratio)


class DeviceSimulator:
    """所有模拟设备的基类, 负责连接、心跳、状态上下线与控制指令分发。"""

    device_type = "device"

    def __init__(self, device_id: str, report_interval: float = 10.0) -> None:
        self.device_id = device_id
        self.report_interval = report_interval
        self.online = True
        self.battery = 92
        self.log = logging.getLogger(f"{self.device_type}:{device_id}")
        self._stop = threading.Event()
        self._handlers: dict[str, Callable[[dict], None]] = {}

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=f"sim-{device_id}"
        )
        if MQTT_USERNAME:
            self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        self.client.will_set(
            self.topic("status"),
            json.dumps({"status": "offline", "ts": now_iso()}),
            qos=1,
            retain=False,
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.register_handlers()

    # ---------- 主题 ----------

    def topic(self, kind: str) -> str:
        return f"{PREFIX}/{self.device_type}/{self.device_id}/{kind}"

    @property
    def control_topic(self) -> str:
        return f"{PREFIX}/sim/{self.device_id}/control"

    # ---------- 生命周期 ----------

    def register_handlers(self) -> None:
        self.on("set_battery", lambda p: setattr(self, "battery", int(p.get("battery", self.battery))))
        self.on("set_online", self._handle_set_online)

    def on(self, action: str, handler: Callable[[dict], None]) -> None:
        self._handlers[action] = handler

    def _handle_set_online(self, payload: dict) -> None:
        self.online = bool(payload.get("online", True))
        self.publish("status", {"status": "online" if self.online else "offline"})
        self.log.info("在线状态切换为 %s", self.online)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):  # noqa: ANN001
        if getattr(reason_code, "is_failure", False):
            self.log.error("连接 Broker 失败: %s", reason_code)
            return
        self.log.info("已连接 MQTT %s:%s", MQTT_HOST, MQTT_PORT)
        client.subscribe(self.control_topic, qos=1)
        for extra in self.extra_subscriptions():
            client.subscribe(extra, qos=1)
        self.publish("status", {"status": "online"})

    def extra_subscriptions(self) -> list[str]:
        return []

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage):  # noqa: ANN001
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if msg.topic == self.control_topic:
            action = payload.get("action", "")
            handler = self._handlers.get(action)
            if handler:
                handler(payload.get("payload", {}))
                self.log.info("执行控制指令: %s", action)
            else:
                self.log.warning("未知控制指令: %s", action)
        else:
            self.handle_topic(msg.topic, payload)

    def handle_topic(self, topic: str, payload: dict) -> None:
        pass

    def publish(self, kind: str, payload: dict[str, Any]) -> None:
        # 标明是模拟器发的：平台据此把演示数据和真机数据分开，也不会对合成数据做坏读数判定
        body = {"device_id": self.device_id, "ts": now_iso(), "source": "simulator", **payload}
        self.client.publish(self.topic(kind), json.dumps(body, ensure_ascii=False), qos=1)

    def build_telemetry(self) -> dict:
        raise NotImplementedError

    def tick(self) -> None:
        pass

    def run(self) -> None:
        self.client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=45)
        self.client.loop_start()
        self.log.info("模拟器启动, 上报间隔 %.1fs", self.report_interval)
        try:
            while not self._stop.is_set():
                if self.online:
                    self.tick()
                    self.publish("telemetry", self.build_telemetry())
                    self.battery = max(0, self.battery - random.choice([0, 0, 0, 1]))
                    if self.battery <= 12:
                        # 长时间演示时电量会耗尽，这里模拟一次充电避免一直是低电量状态
                        self.battery = random.randint(88, 100)
                        self.log.info("电量过低，模拟完成一次充电")
                self._stop.wait(self.report_interval)
        finally:
            self.publish("status", {"status": "offline"})
            time.sleep(0.3)
            self.client.loop_stop()
            self.client.disconnect()
            self.log.info("模拟器已停止")

    def stop(self, *_args) -> None:
        self._stop.set()


def install_signal_handlers(*simulators: DeviceSimulator) -> None:
    def _handler(*_args):
        for sim in simulators:
            sim.stop()

    signal.signal(signal.SIGINT, _handler)
    for name in ("SIGTERM", "SIGBREAK"):
        # SIGBREAK 只有 Windows 有：后端托管启动的模拟器就是靠 Ctrl+Break 事件被叫停的，
        # 不接它进程会被直接杀掉，来不及发 offline 状态
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), _handler)
            except (AttributeError, ValueError):
                pass
