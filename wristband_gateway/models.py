"""手环样本数据结构，只依赖标准库。"""

from dataclasses import dataclass, field
from datetime import datetime

# 体温合理区间；超出这个范围的读数视为表面温度或环境温度，不作为体温
BODY_TEMP_MIN = 35.0
BODY_TEMP_MAX = 45.0

# 生理合理区间。手环没戴在人身上时会输出无意义的数值（实测见过 195/247 的血压），
# 这类值一旦进平台就会触发假告警并拦截正常外出，必须在网关侧就丢掉。
VITAL_RANGES = {
    "heart_rate": (30, 220),
    "blood_oxygen": (70, 100),
    "systolic": (70, 260),
    "diastolic": (40, 160),
    "steps": (0, 200000),
    "battery": (0, 100),
}


def in_range(name: str, value):
    """超出生理区间返回 None，调用方据此丢弃该字段。"""
    if value is None:
        return None
    low, high = VITAL_RANGES[name]
    return value if low <= value <= high else None

PACKET_BROADCAST = "broadcast"
PACKET_RESPONSE_A = "response_a"
PACKET_RESPONSE_B = "response_b"
PACKET_RESPONSE_AB = "response_ab"


@dataclass
class HealthSample:
    """一条手环样本。

    每种包型只带一部分字段，取不到的字段保持 None——
    绝不能用 0 代替，0 在平台侧会被判成"心率过低"这类危险值。
    """

    device_mac: str = ""
    packet_type: str = ""
    timestamp: datetime = field(default_factory=datetime.now)

    heart_rate: int | None = None
    blood_oxygen: int | None = None
    temperature: float | None = None
    systolic: int | None = None
    diastolic: int | None = None
    steps: int | None = None
    battery: int | None = None

    # 原始温度读数，即使不在体温区间也保留，便于排查"温度一直是 0"
    surface_temperature: float | None = None
    ambient_temperature: float | None = None

    sos_flag: bool = False
    sos_trigger: str = ""

    raw_line: str = ""
    # 采集器给出的信号强度（dBm），排查"时断时续"时用来判断是不是距离/遮挡问题
    rssi: int | None = None
    # 被生理区间校验丢掉的字段，诊断时用来区分"没收到"和"收到但无效"
    dropped_fields: list[str] = field(default_factory=list)

    def sanitize(self) -> "HealthSample":
        """把超出生理区间的读数清成 None，原始值仍保留在 raw_line 里。"""
        for name in ("heart_rate", "blood_oxygen", "steps", "battery"):
            raw = getattr(self, name)
            cleaned = in_range(name, raw)
            if raw is not None and cleaned is None:
                self.dropped_fields.append(f"{name}={raw}")
            setattr(self, name, cleaned)

        # 血压是成对测出来的：只要有一个数不可信，整组都不能用
        raw_pair = (self.systolic, self.diastolic)
        systolic = in_range("systolic", self.systolic)
        diastolic = in_range("diastolic", self.diastolic)
        dropped_one = (systolic is None) != (diastolic is None)
        inverted = (
            systolic is not None and diastolic is not None and diastolic >= systolic
        )
        if any(v is not None for v in raw_pair) and (dropped_one or inverted):
            self.dropped_fields.append(f"blood_pressure={raw_pair[0]}/{raw_pair[1]}")
            systolic = diastolic = None
        self.systolic, self.diastolic = systolic, diastolic
        return self

    def is_complete(self) -> bool:
        """同时具备心率、血氧、体温、血压才算完整样本。"""
        return None not in (
            self.heart_rate,
            self.blood_oxygen,
            self.temperature,
            self.systolic,
            self.diastolic,
        )

    def vitals(self) -> dict:
        """只返回有值的生命体征，供上报时组包。"""
        candidates = {
            "heart_rate": self.heart_rate,
            "spo2": self.blood_oxygen,
            "temperature": self.temperature,
            "systolic": self.systolic,
            "diastolic": self.diastolic,
            "steps": self.steps,
            "battery": self.battery,
        }
        return {key: value for key, value in candidates.items() if value is not None}

    def merge_from(self, other: "HealthSample") -> None:
        """把另一条样本里有值的字段补进来，已有值不覆盖。"""
        for name in (
            "heart_rate",
            "blood_oxygen",
            "temperature",
            "systolic",
            "diastolic",
            "steps",
            "battery",
            "surface_temperature",
            "ambient_temperature",
        ):
            if getattr(self, name) is None and getattr(other, name) is not None:
                setattr(self, name, getattr(other, name))
        if other.sos_flag and not self.sos_flag:
            self.sos_flag = True
            self.sos_trigger = other.sos_trigger
        if not self.device_mac:
            self.device_mac = other.device_mac

    def summary(self) -> str:
        def show(value, width=3, fmt="{:>{w}}"):
            return fmt.format("--" if value is None else value, w=width)

        flag = "完整" if self.is_complete() else "部分"
        return (
            f"{self.timestamp:%H:%M:%S}  {self.device_mac or '--':<17}  "
            f"{self.packet_type:<12} "
            f"HR={show(self.heart_rate)}  "
            f"SpO2={show(self.blood_oxygen)}  "
            f"T={show(None if self.temperature is None else f'{self.temperature:.2f}', 5)}  "
            f"BP={show(self.systolic)}/{show(self.diastolic)}  "
            f"步数={show(self.steps, 5)}  "
            f"电量={show(self.battery)}%  [{flag}]"
        )
