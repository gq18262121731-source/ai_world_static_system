"""手环注册表：哪些 MAC 值得采、按什么顺序轮转。

现场的 BLE 环境很吵，采集器什么广播都往串口吐。这里只放行可信的手环，
其余当噪声忽略，免得设备台账被音箱、耳机之类刷爆。

注册表是插入有序的，不是 set——响应板按下标轮转，顺序一变会在停留中途
跳到别的手环上，采到一半的 A 包就等不到配对的 B 包了。
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# T10 出厂 MAC 前缀。别的厂商的手环要另加。
DEFAULT_MAC_PREFIXES = ("54:10:26",)
# 防止 BLE 拥挤的现场把台账刷爆
DEFAULT_MAX_DEVICES = 16
# 自动发现的 MAC 要在短时间内连续看到这么多次才承认。串口在 90 行/秒的流量下偶尔会翻一个位，
# 实测就出现过 51:10:26:01:00:E7（54 翻成 51）；跑上 8 小时后同一个错法也可能凑出 3 次，
# 所以次数必须限定在一个时间窗内：真手环每秒都在广播，一分钟内凑 3 次轻而易举，坏行做不到。
DEFAULT_MIN_SIGHTINGS = 3
DEFAULT_SIGHTING_WINDOW_SECONDS = 60.0


def normalize_mac(mac: str) -> str:
    """54:10:26:01:00:dc / 5410260100DC -> 54:10:26:01:00:DC。"""
    text = (mac or "").replace(":", "").replace("-", "").strip().upper()
    if len(text) != 12:
        return ""
    return ":".join(text[i : i + 2] for i in range(0, 12, 2))


@dataclass
class WristbandRecord:
    mac_address: str
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    sample_count: int = 0
    packet_types: set[str] = field(default_factory=set)
    # 配置里预置的手环永不被拒、永不被淘汰，即使一条数据都没收到过
    preset: bool = False
    # 最近一次信号强度（dBm），采样率偏低时用来分辨是距离问题还是手环问题
    last_rssi: int | None = None

    def as_dict(self) -> dict:
        return {
            "mac_address": self.mac_address,
            "preset": self.preset,
            "sample_count": self.sample_count,
            "last_rssi": self.last_rssi,
            "packet_types": sorted(self.packet_types),
            "first_seen_at": self.first_seen_at.isoformat() if self.first_seen_at else None,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "seconds_since_sample": (
                round((datetime.now() - self.last_seen_at).total_seconds(), 1)
                if self.last_seen_at
                else None
            ),
        }


class WristbandRegistry:
    """线程安全，广播板和响应板两个线程会同时读写。"""

    def __init__(
        self,
        preset_macs: tuple[str, ...] | list[str] = (),
        mac_prefixes: tuple[str, ...] = DEFAULT_MAC_PREFIXES,
        max_devices: int = DEFAULT_MAX_DEVICES,
        auto_discover: bool = True,
        min_sightings: int = DEFAULT_MIN_SIGHTINGS,
        sighting_window_seconds: float = DEFAULT_SIGHTING_WINDOW_SECONDS,
    ) -> None:
        self.mac_prefixes = tuple(normalize_mac(p) or p.upper() for p in mac_prefixes)
        self.max_devices = max_devices
        self.auto_discover = auto_discover
        self.min_sightings = max(1, min_sightings)
        self.sighting_window = timedelta(seconds=sighting_window_seconds)
        self.rejected: dict[str, int] = {}
        # 还没凑够次数的候选 MAC -> 最近一段时间内看到它的时刻
        self._candidates: dict[str, list[datetime]] = {}
        self._items: dict[str, WristbandRecord] = {}
        self._lock = threading.RLock()
        # 预置手环排在轮转队首，响应板不必等广播发现就能开始拉它们的血压
        for mac in preset_macs:
            normalized = normalize_mac(mac)
            if normalized:
                self._items[normalized] = WristbandRecord(mac_address=normalized, preset=True)

    # ---------- 可信性 ----------

    def is_trusted(self, mac: str, signature_ok: bool = False) -> bool:
        normalized = normalize_mac(mac)
        if not normalized:
            return False
        if normalized in self._items:
            return True
        if not self.auto_discover:
            return False
        if signature_ok:
            return True
        return any(normalized.startswith(prefix) for prefix in self.mac_prefixes)

    # ---------- 观测 ----------

    def observe(
        self,
        mac: str,
        packet_type: str = "",
        signature_ok: bool = False,
        rssi: int | None = None,
    ) -> tuple[WristbandRecord | None, bool]:
        """登记一条样本，返回 (记录, 是否首次发现)。

        不可信、或自动发现的 MAC 还没凑够 min_sightings 次，返回 (None, False)，
        调用方应当丢弃这条样本。
        """
        normalized = normalize_mac(mac)
        with self._lock:
            if not self.is_trusted(normalized, signature_ok):
                key = normalized or mac or "<无 MAC>"
                self.rejected[key] = self.rejected.get(key, 0) + 1
                return None, False

            record = self._items.get(normalized)
            is_new = record is None or record.first_seen_at is None
            if record is None:
                now = datetime.now()
                sightings = [t for t in self._candidates.get(normalized, []) if now - t <= self.sighting_window]
                sightings.append(now)
                if len(sightings) < self.min_sightings:
                    self._candidates[normalized] = sightings
                    return None, False
                self._candidates.pop(normalized, None)
                if len(self._items) >= self.max_devices:
                    self.rejected[normalized] = self.rejected.get(normalized, 0) + 1
                    return None, False
                record = WristbandRecord(mac_address=normalized)
                self._items[normalized] = record

            now = datetime.now()
            if record.first_seen_at is None:
                record.first_seen_at = now
            record.last_seen_at = now
            record.sample_count += 1
            if rssi is not None:
                record.last_rssi = rssi
            if packet_type:
                record.packet_types.add(packet_type)
            return record, is_new

    def pending_candidates(self) -> dict[str, int]:
        """还没凑够次数的 MAC 及其在时间窗内的出现次数。长期停在 1 的基本都是串口翻位产生的坏 MAC。"""
        now = datetime.now()
        with self._lock:
            return {
                mac: len([t for t in times if now - t <= self.sighting_window])
                for mac, times in self._candidates.items()
            }

    # ---------- 轮转 ----------

    def target_macs(self) -> list[str]:
        with self._lock:
            return list(self._items)

    def active_macs(self, max_age_seconds: float = 0.0) -> list[str]:
        """只要最近还在发数据的手环。预置手环始终保留，否则没数据就永远轮不到它。"""
        if max_age_seconds <= 0:
            return self.target_macs()
        now = datetime.now()
        with self._lock:
            return [
                mac
                for mac, record in self._items.items()
                if record.preset
                or (
                    record.last_seen_at is not None
                    and (now - record.last_seen_at).total_seconds() <= max_age_seconds
                )
            ]

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [record.as_dict() for record in self._items.values()]

    def __len__(self) -> int:
        return len(self._items)
