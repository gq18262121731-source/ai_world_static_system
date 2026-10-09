"""把手环样本发布到平台的 MQTT 主题。

主题与报文格式和平台既有约定一致（见后端 app/iot/topics.py），
所以平台侧不需要为真机单独写接收逻辑。
"""

import json
import logging
import time
from datetime import datetime

import paho.mqtt.client as mqtt

from .models import HealthSample

logger = logging.getLogger("wristband.publisher")

# 真机数据统一打这个来源标记，和演示用的 simulator 区分开，
# 否则模拟数据会掩盖真实断流（历史上出过三天没人发现的事故）
SOURCE_SERIAL = "serial"


def mac_to_device_id(mac: str) -> str:
    """54:10:26:01:00:DC -> 5410260100DC。

    直接用手环自己的 MAC 作为设备编号，不加前缀：
    这个编号和手环外壳上印的、和 AT+MAC 命令里填的完全一致，
    人工登记时不需要做任何转换，也就不会出现编号对不上导致数据进不来的情况。
    """
    return mac.replace(":", "").replace("-", "").upper()


class SamplePublisher:
    """负责节流、去重与上报。

    完整样本立即发，部分样本按窗口合并后定期发，SOS 立刻发。
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 1883,
        username: str = "",
        password: str = "",
        topic_prefix: str = "aiot",
        gateway_id: str = "wristband-gw-1",
        partial_flush_seconds: float = 5.0,
        sos_dedup_seconds: float = 15.0,
        heartbeat_seconds: float = 10.0,
    ) -> None:
        self.topic_prefix = topic_prefix
        self.gateway_id = gateway_id
        self.partial_flush_seconds = partial_flush_seconds
        self.sos_dedup_seconds = sos_dedup_seconds
        self.heartbeat_seconds = heartbeat_seconds

        self.connected = False
        self.published = 0
        self.dropped_empty = 0

        # device_id -> 待合并的部分样本
        self._pending: dict[str, HealthSample] = {}
        self._last_flush: dict[str, float] = {}
        self._last_sos: dict[str, float] = {}
        self._last_heartbeat = 0.0

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=f"gw-{gateway_id}"
        )
        if username:
            self._client.username_pw_set(username, password)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.reconnect_delay_set(min_delay=1, max_delay=20)
        self._host = host
        self._port = port

    # ---------- 连接 ----------

    def start(self) -> None:
        self._client.connect_async(self._host, self._port, keepalive=45)
        self._client.loop_start()
        logger.info("MQTT 客户端已启动，目标 %s:%s", self._host, self._port)

    def stop(self) -> None:
        self.flush_all()
        self._client.loop_stop()
        try:
            self._client.disconnect()
        except Exception:
            pass

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):  # noqa: ANN001
        if getattr(reason_code, "is_failure", False):
            self.connected = False
            logger.warning("MQTT 连接被拒绝：%s", reason_code)
            return
        self.connected = True
        logger.info("MQTT 已连接")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):  # noqa: ANN001
        self.connected = False
        logger.warning("MQTT 连接断开：%s", reason_code)

    def _publish(self, topic: str, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str)
        result = self._client.publish(topic, body, qos=1)
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            self.published += 1
        else:
            logger.warning("发布失败 rc=%s topic=%s", result.rc, topic)

    # ---------- 样本上报 ----------

    def handle_sample(self, sample: HealthSample) -> None:
        device_id = mac_to_device_id(sample.device_mac)
        if not device_id:
            self.dropped_empty += 1
            return

        if sample.sos_flag:
            self._publish_sos(device_id, sample)

        vitals = sample.vitals()
        if not vitals:
            # 该包一个有效字段都没有，没必要占用一次上报
            self.dropped_empty += 1
            return

        if sample.is_complete():
            self._publish_telemetry(device_id, sample, sample.packet_type)
            self._pending.pop(device_id, None)
            self._last_flush[device_id] = time.monotonic()
            return

        # 部分样本先合并到缓冲，攒到窗口期再发，避免每秒都写库
        pending = self._pending.get(device_id)
        if pending is None:
            self._pending[device_id] = sample
        else:
            pending.merge_from(sample)
            # 血压与体温以新值为准
            for name in ("systolic", "diastolic", "temperature", "steps", "battery"):
                value = getattr(sample, name)
                if value is not None:
                    setattr(pending, name, value)
            pending.timestamp = sample.timestamp
            pending.packet_type = sample.packet_type
        self.maybe_flush(device_id)

    def maybe_flush(self, device_id: str) -> None:
        last = self._last_flush.get(device_id, 0.0)
        if time.monotonic() - last < self.partial_flush_seconds:
            return
        self.flush(device_id)

    def flush(self, device_id: str) -> None:
        sample = self._pending.pop(device_id, None)
        self._last_flush[device_id] = time.monotonic()
        if sample is None:
            return
        if sample.vitals():
            self._publish_telemetry(device_id, sample, "merged")

    def flush_all(self) -> None:
        for device_id in list(self._pending):
            self.flush(device_id)

    def _publish_telemetry(self, device_id: str, sample: HealthSample, packet: str) -> None:
        payload = {
            "ts": sample.timestamp.isoformat(),
            "source": SOURCE_SERIAL,
            "packet": packet,
            "device_mac": sample.device_mac,
            "complete": sample.is_complete(),
            **sample.vitals(),
        }
        raw = {}
        if sample.surface_temperature is not None:
            raw["surface_temperature"] = sample.surface_temperature
        if sample.ambient_temperature is not None:
            raw["ambient_temperature"] = sample.ambient_temperature
        if raw:
            payload["raw"] = raw
        self._publish(f"{self.topic_prefix}/watch/{device_id}/telemetry", payload)

    def _publish_sos(self, device_id: str, sample: HealthSample) -> None:
        now = time.monotonic()
        last = self._last_sos.get(device_id, 0.0)
        if now - last < self.sos_dedup_seconds:
            return
        self._last_sos[device_id] = now
        self._publish(
            f"{self.topic_prefix}/watch/{device_id}/event",
            {
                "ts": sample.timestamp.isoformat(),
                "source": SOURCE_SERIAL,
                "event": "sos",
                "trigger": sample.sos_trigger,
                "device_mac": sample.device_mac,
            },
        )
        logger.warning("上报 SOS：%s（%s）", sample.device_mac, sample.sos_trigger)

    # ---------- 网关心跳 ----------

    def maybe_heartbeat(self, reader_stats: dict, wristbands: list[dict] | None = None) -> None:
        """定期上报串口链路状态，供平台判断真机是否断流。

        `wristbands` 是按手环的采集情况。整体时间戳只要有一只在发就一直在刷新，
        三只里断一只完全看不出来，所以必须逐只上报。
        """
        now = time.monotonic()
        if now - self._last_heartbeat < self.heartbeat_seconds:
            return
        self._last_heartbeat = now
        last_sample = reader_stats.get("last_sample_at")
        self._publish(
            f"{self.topic_prefix}/gateway/{self.gateway_id}/status",
            {
                "ts": datetime.now().isoformat(),
                "source": SOURCE_SERIAL,
                "gateway_id": self.gateway_id,
                "serial_port": reader_stats.get("port", ""),
                "serial_connected": reader_stats.get("connected", False),
                "strategy": reader_stats.get("strategy", ""),
                "lines": reader_stats.get("lines", 0),
                "samples": reader_stats.get("samples", 0),
                "complete_samples": reader_stats.get("complete_samples", 0),
                "filtered_out": reader_stats.get("filtered_out", 0),
                "reconnects": reader_stats.get("reconnects", 0),
                "published": self.published,
                "last_sample_at": (
                    datetime.fromtimestamp(last_sample).isoformat() if last_sample else None
                ),
                "seconds_since_sample": (
                    round(time.time() - last_sample, 1) if last_sample else None
                ),
                "last_error": reader_stats.get("last_error", ""),
                "collectors": reader_stats.get("collectors", []),
                "wristbands": wristbands or [],
            },
        )


def merge_reader_stats(readers: list[tuple[str, dict]]) -> dict:
    """双板时把每块板的计数合成一条心跳，同时保留逐板明细。

    传入 [(角色, reader.stats), ...]。只要有一块板断了就算没连上——
    广播板挂了会丢全部心率血氧，响应板挂了会丢全部血压，两种都不能算健康。
    """
    collectors = [
        {
            "role": role,
            "port": stats.get("port", ""),
            "connected": bool(stats.get("connected")),
            "lines": stats.get("lines", 0),
            "samples": stats.get("samples", 0),
            "reconnects": stats.get("reconnects", 0),
            "current_target": stats.get("current_target", ""),
            "last_error": stats.get("last_error", ""),
        }
        for role, stats in readers
    ]
    sample_times = [s.get("last_sample_at") for _, s in readers if s.get("last_sample_at")]
    errors = [s.get("last_error", "") for _, s in readers if s.get("last_error")]
    return {
        "port": "+".join(item["port"] for item in collectors if item["port"]),
        "connected": all(item["connected"] for item in collectors) if collectors else False,
        "strategy": readers[0][1].get("strategy", "") if readers else "",
        "lines": sum(item["lines"] for item in collectors),
        "samples": sum(item["samples"] for item in collectors),
        "complete_samples": sum(stats.get("complete_samples", 0) for _, stats in readers),
        "filtered_out": sum(stats.get("filtered_out", 0) for _, stats in readers),
        "reconnects": sum(item["reconnects"] for item in collectors),
        "last_sample_at": max(sample_times) if sample_times else None,
        "last_error": errors[0] if errors else "",
        "collectors": collectors,
    }
