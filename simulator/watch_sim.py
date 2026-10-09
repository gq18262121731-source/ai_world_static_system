"""健康手表模拟器。

上报: 心率、血压、血氧、体温、步数、睡眠、电量、位置
事件: 跌倒(fall)、求助(sos)、低电量(low_battery)
"""

import argparse
import random

from common import DeviceSimulator, install_signal_handlers, jitter
from profiles import generate_vitals


class WatchSimulator(DeviceSimulator):
    device_type = "watch"

    def __init__(
        self,
        device_id: str,
        lat: float = 30.2795,
        lng: float = 120.1330,
        interval: float = 10.0,
        profile: str = "normal",
    ) -> None:
        super().__init__(device_id, report_interval=interval)
        self.lat = lat
        self.lng = lng
        self.profile = profile
        self.steps = random.randint(500, 2500)
        self.activity = "rest"

    def register_handlers(self) -> None:
        super().register_handlers()
        self.on("set_profile", self._set_profile)
        self.on("trigger_fall", self._trigger_fall)
        self.on("trigger_sos", self._trigger_sos)
        self.on("set_position", self._set_position)
        self.on("push_now", lambda _p: self.publish("telemetry", self.build_telemetry()))

    def _set_profile(self, payload: dict) -> None:
        self.profile = payload.get("profile", "normal")
        self.log.info("健康档位切换为 %s", self.profile)
        self.publish("telemetry", self.build_telemetry())

    def _trigger_fall(self, payload: dict) -> None:
        self.publish(
            "event",
            {
                "event": "fall",
                "confidence": float(payload.get("confidence", 0.92)),
                "lat": self.lat,
                "lng": self.lng,
                "impact_g": round(random.uniform(2.8, 4.5), 2),
            },
        )
        self.log.warning("上报跌倒事件")

    def _trigger_sos(self, payload: dict) -> None:
        self.publish(
            "event",
            {"event": "sos", "lat": self.lat, "lng": self.lng, "note": payload.get("note", "")},
        )

    def _set_position(self, payload: dict) -> None:
        self.lat = float(payload.get("lat", self.lat))
        self.lng = float(payload.get("lng", self.lng))

    def tick(self) -> None:
        self.activity = random.choices(
            ["rest", "light", "moderate"], weights=[0.6, 0.3, 0.1]
        )[0]
        self.steps += {"rest": random.randint(0, 12), "light": random.randint(20, 60), "moderate": random.randint(60, 140)}[
            self.activity
        ]
        if self.battery in (20, 10, 5):
            self.publish("event", {"event": "low_battery", "battery": self.battery})

    def build_telemetry(self) -> dict:
        vitals = generate_vitals(self.profile)
        if self.activity == "moderate":
            vitals["heart_rate"] = round(jitter(vitals["heart_rate"] + 12, 0.04))
        return {
            **vitals,
            "steps": self.steps,
            "activity_level": self.activity,
            "battery": self.battery,
            "lat": round(self.lat, 6),
            "lng": round(self.lng, 6),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="健康手表模拟器")
    parser.add_argument("--device-id", default="WATCH-LJG-001")
    parser.add_argument("--lat", type=float, default=30.2795)
    parser.add_argument("--lng", type=float, default=120.1330)
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--profile", default="normal")
    args = parser.parse_args()

    sim = WatchSimulator(
        args.device_id, args.lat, args.lng, args.interval, args.profile
    )
    install_signal_handlers(sim)
    sim.run()


if __name__ == "__main__":
    main()
