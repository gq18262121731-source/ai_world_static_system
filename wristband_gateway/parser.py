"""T10 手环报文解析。

协议来源见项目文档「T10 手环串口采集」第三节。采集器每收到一条手环报文，
输出一行 HEX 文本，格式为 <MAC 12 位><载荷>，也可能带 RAW,ID=xxxx,DATA= 之类前缀。

手环分三种包，各带一部分字段；A 包与 B 包由同一次连接先后返回，
在 merge_timeout_seconds 内合并才能得到唯一一种同时含心率/血氧/体温/血压的完整样本。
"""

import re
import threading
from datetime import datetime, timedelta

from .models import (
    BODY_TEMP_MAX,
    BODY_TEMP_MIN,
    PACKET_BROADCAST,
    PACKET_RESPONSE_A,
    PACKET_RESPONSE_AB,
    PACKET_RESPONSE_B,
    HealthSample,
)

BROADCAST_PREFIX = "0201061AFF4C000215"
RESPONSE_A_MARK = "161803"
RESPONSE_B_MARK = "160318"

# T10 手环的身份特征：广播包带 Radioland-T10 的 UUID，响应包带 "T10WATCH" 名称。
# 附近其他 BLE 设备的载荷有概率碰巧含有 160318 这样的标记，
# 实测就有一个音箱和一个未知设备被误判成手环，必须靠特征把它们挡掉。
T10_UUID_HEX = "526164696F6C616E642D5410"
T10_NAME_HEX = "5431305741544348"  # ASCII "T10WATCH"
# 旧固件在偏移 12 字节处放两字节标记，不带 16 前缀
LEGACY_A_MARK = "1803"
LEGACY_B_MARK = "0318"
LEGACY_MARK_OFFSET = 12

HEX_LINE = re.compile(r"[0-9A-F]{8,}")
# 采集器可能给数据行加前缀，例如 RAW,ID=1234,DATA=xxxx
PREFIX_PATTERNS = [
    re.compile(r"^.*DATA\s*=\s*", re.IGNORECASE),
    re.compile(r"^RAW\s*,?\s*", re.IGNORECASE),
]


def _u16(payload: bytes, offset: int) -> int:
    """大端读两字节。"""
    return (payload[offset] << 8) | payload[offset + 1]


def _temp(payload: bytes, offset: int) -> float:
    return _u16(payload, offset) / 100.0


def _as_body_temperature(value: float | None) -> float | None:
    """只有落在体温区间的读数才算体温，否则是表面温或环境温。"""
    if value is None:
        return None
    return value if BODY_TEMP_MIN <= value <= BODY_TEMP_MAX else None


def _as_rssi(value: int) -> int:
    """采集器给的前导字节是有符号的 dBm，0xBF 表示 -65。"""
    return value - 256 if value > 127 else value


def _format_mac(raw: bytes | str) -> str:
    if isinstance(raw, bytes):
        text = raw.hex().upper()
    else:
        text = raw.replace(":", "").upper()
    if len(text) != 12:
        return ""
    return ":".join(text[i : i + 2] for i in range(0, 12, 2))


class T10PacketParser:
    """把一行 HEX 文本解析成 HealthSample。

    A 包会被暂存，等待 B 包在超时窗口内到达后合并；B 包超时才到就单独产出。
    暂存是按 MAC 分桶的：多只手环并发时共用一个槽，会把甲的 A 和乙的 B 合成一条
    看起来完全正常、实际张冠李戴的样本。

    实例可以被多个采集线程共用（双板方案就是这么用的），feed 与 flush 都持锁。
    """

    def __init__(
        self,
        merge_timeout_seconds: float = 0.5,
        fallback_device_mac: str = "",
        require_t10_signature: bool = True,
    ) -> None:
        self.merge_timeout_seconds = merge_timeout_seconds
        self.fallback_device_mac = _format_mac(fallback_device_mac) if fallback_device_mac else ""
        self.require_t10_signature = require_t10_signature
        self.rejected_foreign = 0
        self.last_rssi: int | None = None
        # MAC -> (待合并的 A 包, 暂存时刻)
        self._partials: dict[str, tuple[HealthSample, datetime]] = {}
        self._lock = threading.RLock()

    # ---------- 行处理 ----------

    def extract_payload_and_mac(self, line: str) -> tuple[bytes, str]:
        """剥掉前缀、认出行首 MAC，返回 (载荷字节, MAC)。

        实测采集器输出的是 <1 字节 RSSI><6 字节 MAC><AD 结构>，
        比文档记载的 <MAC><载荷> 多一个前导字节，两种都要认。
        """
        text = line.strip().upper().replace(" ", "").replace("-", "")
        for pattern in PREFIX_PATTERNS:
            text = pattern.sub("", text)
        match = HEX_LINE.search(text)
        if not match:
            return b"", ""
        hex_text = match.group(0)
        if len(hex_text) % 2:
            hex_text = hex_text[:-1]

        try:
            data = bytes.fromhex(hex_text)
        except ValueError:
            return b"", ""

        # 「纯 MAC」与「RSSI+MAC」两种排布都打一次分，取更像的那个
        best_score, best_rest, best_mac, best_lead = 0, b"", "", 0
        for lead in (0, 1):
            if len(data) < lead + 8:
                continue
            rest = data[lead + 6 :]
            score = _layout_score(rest)
            if score > best_score:
                best_score = score
                best_rest = rest
                best_mac = _format_mac(data[lead : lead + 6])
                best_lead = lead

        if best_score > 0:
            # 有前导字节时它是有符号的信号强度，留给诊断用
            self.last_rssi = _as_rssi(data[0]) if best_lead == 1 else None
            return best_rest, best_mac
        self.last_rssi = None
        # 认不出 MAC 时整行当载荷，交给后续按标记定位
        return data, ""

    def feed(self, line: str) -> list[HealthSample]:
        """喂一行原始文本，返回本行产生的样本（可能 0~2 条）。"""
        with self._lock:
            return self._feed_locked(line)

    def _feed_locked(self, line: str) -> list[HealthSample]:
        # 超时检查要在解析之前做，否则 AT 回显这类空行会让挂起的 A 包一直发不出去
        samples: list[HealthSample] = self._flush_expired_a()

        payload, mac = self.extract_payload_and_mac(line)
        if not payload:
            return samples

        hex_text = payload.hex().upper()
        if self.require_t10_signature and not _has_t10_signature(hex_text):
            self.rejected_foreign += 1
            return samples
        if hex_text.startswith(BROADCAST_PREFIX):
            sample = self._finalize(self._parse_broadcast(payload, mac))
            if sample:
                sample.raw_line = line.strip()
                samples.append(sample)
            return samples

        index_a = hex_text.find(RESPONSE_A_MARK)
        index_b = hex_text.find(RESPONSE_B_MARK)
        # 同一行里两个标记都出现时，取靠前的那个作为本包类型
        if index_a >= 0 and index_b >= 0 and index_b < index_a:
            index_a = -1
        # find 返回的是十六进制字符位置，除以 2 才是字节偏移
        if index_a >= 0 and index_a % 2 == 0:
            sample = self._finalize(self._parse_response_a(payload[index_a // 2 :], mac))
            if sample:
                sample.raw_line = line.strip()
                displaced = self._stash_a(sample)
                if displaced:
                    samples.append(displaced)
                return samples
        if index_b >= 0 and index_b % 2 == 0:
            sample = self._finalize(self._parse_response_b(payload[index_b // 2 :], mac))
            if sample:
                sample.raw_line = line.strip()
                samples.append(self._merge_with_pending(sample))
                return samples

        legacy = self._finalize(self._parse_legacy(payload, mac))
        if legacy:
            legacy.raw_line = line.strip()
            if legacy.packet_type == PACKET_RESPONSE_A:
                displaced = self._stash_a(legacy)
                if displaced:
                    samples.append(displaced)
            else:
                samples.append(self._merge_with_pending(legacy))
        return samples

    def flush(self) -> list[HealthSample]:
        """收尾时把还挂着的 A 包吐出来。"""
        with self._lock:
            pending = [item[0] for item in self._partials.values()]
            self._partials.clear()
            return pending

    # ---------- 合并 ----------

    def _stash_a(self, sample: HealthSample) -> HealthSample | None:
        """暂存 A 包等 B 包。同一只手环连来两个 A 时把前一个让出去，别悄悄丢。"""
        previous = self._partials.get(sample.device_mac)
        self._partials[sample.device_mac] = (sample, sample.timestamp)
        return previous[0] if previous else None

    def _flush_expired_a(self) -> list[HealthSample]:
        now = datetime.now()
        expired = []
        for mac, (sample, stashed_at) in list(self._partials.items()):
            if now > stashed_at + timedelta(seconds=self.merge_timeout_seconds):
                expired.append(sample)
                del self._partials[mac]
        return expired

    def _pending_key_for(self, mac: str) -> str | None:
        """B 包本身不含 MAC，行首也没认出来时，只有全场恰好挂着一个 A 才敢合并。"""
        if mac and mac in self._partials:
            return mac
        if not mac and len(self._partials) == 1:
            return next(iter(self._partials))
        return None

    def _merge_with_pending(self, sample_b: HealthSample) -> HealthSample:
        key = self._pending_key_for(sample_b.device_mac)
        if key is None:
            return sample_b
        pending, stashed_at = self._partials[key]
        if sample_b.timestamp - stashed_at > timedelta(seconds=self.merge_timeout_seconds):
            # 超窗，A 单独作废由调用方的 _flush_expired_a 处理，这里只返回 B
            return sample_b
        del self._partials[key]
        merged = pending
        merged.merge_from(sample_b)
        # B 包带的体温更可信，存在时覆盖 A 的表面温推断值
        if sample_b.temperature is not None:
            merged.temperature = sample_b.temperature
        merged.systolic = sample_b.systolic
        merged.diastolic = sample_b.diastolic
        merged.packet_type = PACKET_RESPONSE_AB
        merged.timestamp = sample_b.timestamp
        return merged

    # ---------- 各包型 ----------

    def _finalize(self, sample: HealthSample | None) -> HealthSample | None:
        if sample is None:
            return None
        sample.rssi = self.last_rssi
        return sample.sanitize()

    def _parse_broadcast(self, payload: bytes, mac: str) -> HealthSample | None:
        # 需要读到偏移 28（温度低字节），因此至少 29 字节
        if len(payload) < 29:
            return None
        sample = HealthSample(
            device_mac=mac or self.fallback_device_mac,
            packet_type=PACKET_BROADCAST,
        )
        sample.heart_rate = payload[25] or None
        sample.blood_oxygen = payload[26] or None
        raw_temp = _temp(payload, 27)
        sample.surface_temperature = raw_temp
        sample.temperature = _as_body_temperature(raw_temp)

        # SOS 位在偏移 29，短包没有这一字节
        if len(payload) >= 30:
            flags = payload[29]
            if flags & 0b10:
                sample.sos_flag = True
                sample.sos_trigger = "long_press"
            elif flags & 0b01:
                sample.sos_flag = True
                sample.sos_trigger = "double_click"
        return sample

    def _parse_response_a(self, payload: bytes, mac: str) -> HealthSample | None:
        if len(payload) < 20:
            return None
        sample = HealthSample(packet_type=PACKET_RESPONSE_A)
        sample.ambient_temperature = _temp(payload, 3)
        sample.battery = payload[5]
        surface = _temp(payload, 6)
        sample.surface_temperature = surface
        sample.temperature = _as_body_temperature(surface)
        sample.heart_rate = _u16(payload, 8) or None
        sample.blood_oxygen = _u16(payload, 10) or None
        sample.device_mac = mac or _format_mac(payload[12:18]) or self.fallback_device_mac
        sample.steps = _u16(payload, 18)
        return sample

    def _parse_response_b(self, payload: bytes, mac: str) -> HealthSample | None:
        if len(payload) < 5:
            return None
        sample = HealthSample(
            device_mac=mac or self.fallback_device_mac,
            packet_type=PACKET_RESPONSE_B,
        )
        sample.systolic = payload[3] or None
        sample.diastolic = payload[4] or None
        if len(payload) >= 7:
            raw_temp = _temp(payload, 5)
            sample.surface_temperature = raw_temp
            sample.temperature = _as_body_temperature(raw_temp)
        return sample

    def _parse_legacy(self, payload: bytes, mac: str) -> HealthSample | None:
        """旧固件：偏移 12 处放两字节标记，不带 16 前缀。"""
        if len(payload) < LEGACY_MARK_OFFSET + 2:
            return None
        mark = payload[LEGACY_MARK_OFFSET : LEGACY_MARK_OFFSET + 2].hex().upper()
        # 构造出与新固件一致的相对偏移：标记前补一字节，凑成 3 字节标记头
        if mark == LEGACY_A_MARK:
            shifted = b"\x16" + payload[LEGACY_MARK_OFFSET:]
            return self._parse_response_a(shifted, mac)
        if mark == LEGACY_B_MARK:
            shifted = b"\x16" + payload[LEGACY_MARK_OFFSET:]
            return self._parse_response_b(shifted, mac)
        return None


def _count_ad_structures(data: bytes) -> int:
    """按 BLE 的「长度+类型+数据」链走一遍，返回能完整走通的结构数。

    走不通说明这段字节不是从 AD 结构开头的，也就说明 MAC 边界猜错了。
    """
    index = 0
    count = 0
    while index < len(data):
        length = data[index]
        if length == 0:
            break  # 补零填充，正常结束
        if length > 31 or index + 1 + length > len(data):
            return 0  # 越界，排布不对
        count += 1
        index += 1 + length
    return count


def _layout_score(rest: bytes) -> int:
    """给一种 MAC 边界猜测打分，分越高越可能是正确排布。"""
    if len(rest) < 3:
        return 0
    hex_text = rest.hex().upper()
    score = 0
    if hex_text.startswith(BROADCAST_PREFIX):
        score += 100
    if hex_text.startswith(RESPONSE_A_MARK) or hex_text.startswith(RESPONSE_B_MARK):
        score += 60
    score += 10 * _count_ad_structures(rest)
    if _looks_like_response(hex_text):
        score += 5
    return score


def _looks_like_response(hex_text: str) -> bool:
    return RESPONSE_A_MARK in hex_text or RESPONSE_B_MARK in hex_text


def _has_t10_signature(hex_text: str) -> bool:
    return T10_UUID_HEX in hex_text or T10_NAME_HEX in hex_text
