"""陪护机器狗模拟器。

上报: 位置、速度、电量、跟随状态
事件: 跌倒检测(fall_detected)、跟随丢失(follow_lost)
语音: 把老人说的话作为 ASR 结果上行到平台
下行: tts_speak / start_follow / stop_follow / return_home / ask_confirm / alarm_broadcast
"""

import argparse
import math
import os
import random

from common import PREFIX, DeviceSimulator, install_signal_handlers


class DogSimulator(DeviceSimulator):
    device_type = "dog"

    def __init__(
        self,
        device_id: str,
        lat: float = 30.2795,
        lng: float = 120.1330,
        interval: float = 5.0,
    ) -> None:
        super().__init__(device_id, report_interval=interval)
        self.home_lat = lat
        self.home_lng = lng
        self.lat = lat
        self.lng = lng
        self.follow_mode = False
        self.walking = False
        self.task_id: int | None = None
        self.heading = random.uniform(0, 2 * math.pi)
        self.speed = 0.0
        self.battery = 88

    # ---------- 订阅平台下行指令 ----------

    def extra_subscriptions(self) -> list[str]:
        return [f"{PREFIX}/dog/{self.device_id}/cmd"]

    def handle_topic(self, topic: str, payload: dict) -> None:
        command = payload.get("command", "")
        body = payload.get("payload", {})
        if command == "tts_speak":
            self.log.info("🔊 播报: %s", body.get("text", ""))
        elif command == "ask_confirm":
            self.log.warning("🔊 确认询问: %s", body.get("text", ""))
        elif command == "alarm_broadcast":
            self.log.error("🚨 告警播报: %s", body.get("text", ""))
        elif command == "start_follow":
            self.follow_mode = True
            self.walking = True
            self.task_id = body.get("task_id")
            self.home_lat, self.home_lng = self.lat, self.lng
            self.log.info(
                "进入跟随模式: 任务#%s, 限时 %s 分钟, 半径 %s 米",
                self.task_id,
                body.get("duration_minutes"),
                body.get("radius_m"),
            )
        elif command == "stop_follow":
            self.follow_mode = False
            self.walking = False
            self.task_id = None
            self.log.info("退出跟随模式")
        elif command == "return_home":
            self.walking = True
            self.log.info("开始返航")
        elif command == "ping":
            # 平台自检：原样回带 nonce，证明本机确实能收指令
            self.publish(
                "event",
                {
                    "event": "pong",
                    "nonce": body.get("nonce", ""),
                    "battery": self.battery,
                    "follow_mode": self.follow_mode,
                },
            )
            self.log.info("响应平台探活")
        else:
            self.log.info("收到指令 %s: %s", command, body)

    # ---------- 模拟器控制指令 ----------

    def register_handlers(self) -> None:
        super().register_handlers()
        self.on("say", self._say)
        self.on("trigger_fall", self._trigger_fall)
        self.on("follow_lost", self._follow_lost)
        self.on("walk", self._walk)
        self.on("set_position", self._set_position)

    def _say(self, payload: dict) -> None:
        text = str(payload.get("text", "")).strip()
        if not text:
            return
        self.log.info("🎤 听到老人说: %s", text)
        self.publish("speech", {"text": text, "asr_confidence": 0.96})

    def _trigger_fall(self, payload: dict) -> None:
        self.publish(
            "event",
            {
                "event": "fall_detected",
                "confidence": float(payload.get("confidence", 0.88)),
                "lat": self.lat,
                "lng": self.lng,
                "detector": "vision+imu",
            },
        )
        self.log.warning("视觉检测到老人跌倒")

    def _follow_lost(self, payload: dict) -> None:
        self.publish(
            "event",
            {
                "event": "follow_lost",
                "detail": payload.get("detail", "目标离开视野超过 15 秒"),
                "lat": self.lat,
                "lng": self.lng,
            },
        )

    def _walk(self, payload: dict) -> None:
        self.walking = bool(payload.get("walking", True))
        if "meters" in payload:
            self._advance(float(payload["meters"]))

    def _set_position(self, payload: dict) -> None:
        self.lat = float(payload.get("lat", self.lat))
        self.lng = float(payload.get("lng", self.lng))

    # ---------- 运动模型 ----------

    def _advance(self, meters: float) -> None:
        self.heading += random.uniform(-0.5, 0.5)
        self.lat += meters * math.cos(self.heading) / 111320
        self.lng += meters * math.sin(self.heading) / 92000

    def tick(self) -> None:
        if self.walking:
            # 老人步行速度约 0.8 m/s
            self.speed = round(random.uniform(0.6, 1.1), 2)
            self._advance(self.speed * self.report_interval)
        else:
            self.speed = 0.0

    def build_telemetry(self) -> dict:
        return {
            "lat": round(self.lat, 6),
            "lng": round(self.lng, 6),
            "speed": self.speed,
            "battery": self.battery,
            "follow_mode": self.follow_mode,
            "gait": "walk" if self.walking else "idle",
            "task_id": self.task_id,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="陪护机器狗模拟器")
    parser.add_argument("--device-id", default=os.getenv("DOG_DEVICE_ID", "DOG-LJG-001"))
    parser.add_argument("--lat", type=float, default=30.2795)
    parser.add_argument("--lng", type=float, default=120.1330)
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args()

    sim = DogSimulator(args.device_id, args.lat, args.lng, args.interval)
    install_signal_handlers(sim)
    sim.run()


if __name__ == "__main__":
    main()
