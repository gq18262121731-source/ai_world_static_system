"""解析器离线自测，不需要硬件。

改动 parser.py 后先跑这个：python -m wristband_gateway.selftest
"""

import sys
import time

from .device_discovery import WristbandRegistry
from .models import (
    PACKET_BROADCAST,
    PACKET_RESPONSE_A,
    PACKET_RESPONSE_AB,
    PACKET_RESPONSE_B,
)
from .parser import BROADCAST_PREFIX, T10PacketParser
from .serial_reader import (
    ROLE_BROADCAST,
    ROLE_RESPONSE,
    ROLE_ROTATE,
    TYPE_BROADCAST,
    TYPE_RESPONSE,
    _WindowScheduler,
)

MAC = "5410260100DC"
MAC_FMT = "54:10:26:01:00:DC"
# 现场的第二只手环，用来验证多手环并发时不会串包
MAC_2 = "5410260100DF"
MAC_2_FMT = "54:10:26:01:00:DF"
UUID = "526164696F6C616E642D541000000000"


def build_broadcast(hr: int, spo2: int, temp_x100: int, sos: int = 0x00, mac: str = MAC) -> str:
    """按第 3.1 节拼一条广播包。

    真机的广播包一定带 SOS 字节（无按键时为 00），少了这一字节 AD 长度就对不上。
    """
    body = f"{hr:02X}{spo2:02X}{temp_x100:04X}{sos:02X}"
    return mac + BROADCAST_PREFIX + UUID + body


# 响应包里的设备名 AD 结构，既是真机格式，也是解析器认手环的特征之一
NAME_AD = "09095431305741544348"


def build_response_a(
    ambient_x100: int,
    battery: int,
    surface_x100: int,
    hr: int,
    spo2: int,
    steps: int,
    mac: str = MAC,
) -> str:
    """按第 3.2 节拼一条响应 A 包。

    标记前的 14 是 BLE 的 AD 长度字节（1 字节类型 + 2 字节 UUID + 17 字节数据），
    真机报文里就是这个结构，少了它 MAC 边界会判错。
    """
    return (
        mac
        + NAME_AD
        + "14"
        + "161803"
        + f"{ambient_x100:04X}"
        + f"{battery:02X}"
        + f"{surface_x100:04X}"
        + f"{hr:04X}"
        + f"{spo2:04X}"
        + mac
        + f"{steps:04X}"
    )


def build_response_b(
    systolic: int, diastolic: int, temp_x100: int | None = None, mac: str = MAC
) -> str:
    """按第 3.3 节拼一条响应 B 包。"""
    body = f"{systolic:02X}{diastolic:02X}"
    if temp_x100 is not None:
        body += f"{temp_x100:04X}"
    ad_length = 1 + 2 + len(body) // 2  # 类型 + UUID + 数据
    return mac + NAME_AD + f"{ad_length:02X}" + "160318" + body


class Checker:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = []

    def check(self, label: str, actual, expected) -> None:
        if actual == expected:
            self.passed += 1
        else:
            self.failed.append(f"{label}: 期望 {expected!r}，实际 {actual!r}")

    def report(self) -> int:
        total = self.passed + len(self.failed)
        print(f"\n断言 {self.passed}/{total} 通过")
        for item in self.failed:
            print(f"  失败 · {item}")
        return 0 if not self.failed else 1


def main() -> int:
    check = Checker()

    # ---------- 广播包 ----------
    print("广播包（心率/血氧/温度/SOS）")
    parser = T10PacketParser()
    samples = parser.feed(build_broadcast(87, 97, 3646))
    check.check("广播包产出 1 条", len(samples), 1)
    sample = samples[0]
    check.check("包型", sample.packet_type, PACKET_BROADCAST)
    check.check("MAC", sample.device_mac, MAC_FMT)
    check.check("心率", sample.heart_rate, 87)
    check.check("血氧", sample.blood_oxygen, 97)
    check.check("体温", sample.temperature, 36.46)
    check.check("无 SOS", sample.sos_flag, False)
    check.check("广播包无血压", sample.systolic, None)
    print(f"  {sample.summary()}")

    # 温度超出体温区间时不作为体温，但原值保留
    samples = parser.feed(build_broadcast(80, 96, 2410))
    check.check("环境温不算体温", samples[0].temperature, None)
    check.check("原值保留在表面温", samples[0].surface_temperature, 24.10)

    # 心率血氧为 0 表示该包没带，转成 None 而不是 0
    samples = parser.feed(build_broadcast(0, 0, 3650))
    check.check("心率 0 转 None", samples[0].heart_rate, None)
    check.check("血氧 0 转 None", samples[0].blood_oxygen, None)

    # SOS 位
    samples = parser.feed(build_broadcast(85, 98, 3640, sos=0x02))
    check.check("长按 SOS", samples[0].sos_flag, True)
    check.check("长按类型", samples[0].sos_trigger, "long_press")
    samples = parser.feed(build_broadcast(85, 98, 3640, sos=0x01))
    check.check("双击类型", samples[0].sos_trigger, "double_click")
    samples = parser.feed(build_broadcast(85, 98, 3640, sos=0x81))
    check.check("高位带状态仍识别双击", samples[0].sos_trigger, "double_click")
    samples = parser.feed(build_broadcast(85, 98, 3640, sos=0x00))
    check.check("无按键不触发", samples[0].sos_flag, False)

    # ---------- 响应 A ----------
    print("\n响应 A 包（电量/步数/心率/血氧）")
    parser = T10PacketParser()
    result = parser.feed(build_response_a(2410, 90, 3652, 87, 99, 265))
    check.check("A 包先缓存不产出", len(result), 0)
    pending = parser.flush()
    check.check("flush 取回 A 包", len(pending), 1)
    sample = pending[0]
    check.check("A 包型", sample.packet_type, PACKET_RESPONSE_A)
    check.check("电量", sample.battery, 90)
    check.check("步数", sample.steps, 265)
    check.check("心率", sample.heart_rate, 87)
    check.check("血氧", sample.blood_oxygen, 99)
    check.check("表面温作体温", sample.temperature, 36.52)
    check.check("环境温", sample.ambient_temperature, 24.10)
    check.check("A 包无血压", sample.systolic, None)
    print(f"  {sample.summary()}")

    # ---------- 响应 B ----------
    print("\n响应 B 包（血压）")
    parser = T10PacketParser()
    samples = parser.feed(build_response_b(119, 79))
    check.check("B 包直接产出", len(samples), 1)
    sample = samples[0]
    check.check("B 包型", sample.packet_type, PACKET_RESPONSE_B)
    check.check("收缩压", sample.systolic, 119)
    check.check("舒张压", sample.diastolic, 79)
    check.check("B 包无心率", sample.heart_rate, None)
    print(f"  {sample.summary()}")

    # ---------- A/B 合并 ----------
    print("\nA + B 合并（唯一的完整样本）")
    parser = T10PacketParser(merge_timeout_seconds=0.5)
    parser.feed(build_response_a(2410, 90, 3652, 87, 99, 265))
    samples = parser.feed(build_response_b(119, 79))
    check.check("合并产出 1 条", len(samples), 1)
    merged = samples[0]
    check.check("合并包型", merged.packet_type, PACKET_RESPONSE_AB)
    check.check("合并后心率", merged.heart_rate, 87)
    check.check("合并后血氧", merged.blood_oxygen, 99)
    check.check("合并后收缩压", merged.systolic, 119)
    check.check("合并后舒张压", merged.diastolic, 79)
    check.check("合并后步数", merged.steps, 265)
    check.check("合并后电量", merged.battery, 90)
    check.check("是完整样本", merged.is_complete(), True)
    print(f"  {merged.summary()}")

    # B 包自带体温时覆盖 A 的推断值
    parser = T10PacketParser(merge_timeout_seconds=0.5)
    parser.feed(build_response_a(2410, 90, 3652, 87, 99, 265))
    merged = parser.feed(build_response_b(119, 79, 3680))[0]
    check.check("B 包体温覆盖 A", merged.temperature, 36.80)

    # ---------- 合并超时 ----------
    print("\n合并超时：A 单独产出，B 单独产出")
    parser = T10PacketParser(merge_timeout_seconds=0.2)
    parser.feed(build_response_a(2410, 90, 3652, 87, 99, 265))
    time.sleep(0.35)
    samples = parser.feed(build_response_b(131, 72))
    check.check("超时后产出 2 条", len(samples), 2)
    kinds = sorted(item.packet_type for item in samples)
    check.check("两条分别是 A 和 B", kinds, [PACKET_RESPONSE_A, PACKET_RESPONSE_B])
    lone_b = next(s for s in samples if s.packet_type == PACKET_RESPONSE_B)
    check.check("单独 B 包心率为空", lone_b.heart_rate, None)
    check.check("单独 B 包不完整", lone_b.is_complete(), False)

    # ---------- 行前缀与脏数据 ----------
    print("\n行前缀与异常输入")
    parser = T10PacketParser()
    samples = parser.feed(f"RAW,ID=0042,DATA={build_broadcast(75, 98, 3655)}")
    check.check("带 RAW 前缀能解析", len(samples), 1)
    check.check("带前缀心率正确", samples[0].heart_rate, 75)

    samples = parser.feed("OK")
    check.check("AT 回显不产出样本", len(samples), 0)
    samples = parser.feed("")
    check.check("空行不产出样本", len(samples), 0)
    samples = parser.feed("ZZZZNOTHEX")
    check.check("非法字符不产出样本", len(samples), 0)
    samples = parser.feed(MAC + "1618")
    check.check("过短的包不产出样本", len(samples), 0)

    # 行首无 MAC 时用兜底 MAC
    parser = T10PacketParser(fallback_device_mac=MAC)
    samples = parser.feed(BROADCAST_PREFIX + UUID + "50623640" + "00")
    check.check("兜底 MAC 生效", samples[0].device_mac, MAC_FMT)

    # ---------- 真机抓到的原始报文 ----------
    # 实测采集器输出是 <1字节RSSI><6字节MAC><AD结构>，比文档多一个前导字节
    print("\n真机报文（含 RSSI 前导字节）")
    parser = T10PacketParser()
    live_b = "BF5410260100DF0909543130574154434807160318784B0A8C00000000000000000000000000"
    samples = parser.feed(live_b)
    check.check("真机 B 包产出 1 条", len(samples), 1)
    sample = samples[0]
    check.check("真机 B 包 MAC 跳过 RSSI", sample.device_mac, "54:10:26:01:00:DF")
    check.check("真机收缩压", sample.systolic, 120)
    check.check("真机舒张压", sample.diastolic, 75)
    check.check("27℃ 不作为体温", sample.temperature, None)
    print(f"  {sample.summary()}")

    parser = T10PacketParser()
    live_a = "B65410260100E709095431305741544348141618030000620000005A00005410260100E70000"
    parser.feed(live_a)
    pending = parser.flush()
    check.check("真机 A 包解析出 1 条", len(pending), 1)
    sample = pending[0]
    check.check("真机 A 包 MAC", sample.device_mac, "54:10:26:01:00:E7")
    check.check("真机电量", sample.battery, 98)
    check.check("真机心率", sample.heart_rate, 90)
    check.check("真机血氧 0 转 None", sample.blood_oxygen, None)
    print(f"  {sample.summary()}")

    # AT 回显不应挡住已超时 A 包的产出
    parser = T10PacketParser(merge_timeout_seconds=0.15)
    parser.feed(live_a)
    time.sleep(0.25)
    samples = parser.feed("AT+SCANSTOP=OK")
    check.check("AT 回显时也能吐出超时的 A 包", len(samples), 1)

    # ---------- 排除周边其他 BLE 设备 ----------
    print("\n周边设备与无效读数")
    parser = T10PacketParser()
    noise = parser.feed("C06C1629EF444C0C094544494649455220424C45")
    check.check("蓝牙音箱广播不产出样本", len(noise), 0)
    # 实测有个 22:01:07:00:01:31 的设备载荷碰巧含 160318，被误判成手环
    foreign = parser.feed("B8220107000131110955532D31323334160318010200000000")
    check.check("载荷碰巧含标记的陌生设备被挡掉", len(foreign), 0)
    check.check("陌生设备计入拒绝数", parser.rejected_foreign > 0, True)

    # ---------- 未佩戴时的无效读数 ----------
    # 实测 54:10:26:01:00:DC 未佩戴时报出 195/247，舒张压高于收缩压不可能
    parser = T10PacketParser()
    samples = parser.feed(build_response_b(195, 247))
    check.check("舒张压高于收缩压时整组丢弃", samples[0].systolic, None)
    check.check("舒张压同时丢弃", samples[0].diastolic, None)

    samples = parser.feed(build_response_b(120, 78))
    check.check("正常血压保留", (samples[0].systolic, samples[0].diastolic), (120, 78))

    parser = T10PacketParser()
    parser.feed(build_response_a(2410, 200, 3652, 260, 99, 265))
    sample = parser.flush()[0]
    check.check("心率 260 超出生理区间被丢弃", sample.heart_rate, None)
    check.check("电量 200 超出区间被丢弃", sample.battery, None)
    check.check("同包内正常字段不受影响", sample.blood_oxygen, 99)

    # ---------- 多手环并发 ----------
    # A/B 缓存共用一个槽时，甲的 A 会和乙的 B 合成一条看起来完全正常的假样本
    print("\n多手环并发（A/B 缓存按 MAC 分桶）")
    parser = T10PacketParser(merge_timeout_seconds=5.0)
    parser.feed(build_response_a(2410, 90, 3652, 87, 99, 265, mac=MAC))
    parser.feed(build_response_a(2400, 55, 3670, 66, 95, 100, mac=MAC_2))
    samples = parser.feed(build_response_b(119, 79, mac=MAC_2))
    check.check("乙的 B 只产出一条", len(samples), 1)
    merged = samples[0]
    check.check("合并结果归属乙", merged.device_mac, MAC_2_FMT)
    check.check("用的是乙的心率", merged.heart_rate, 66)
    check.check("用的是乙的电量", merged.battery, 55)
    check.check("用的是乙的步数", merged.steps, 100)
    print(f"  {merged.summary()}")

    remaining = parser.flush()
    check.check("甲的 A 没被乙顶掉", len(remaining), 1)
    check.check("挂着的仍是甲", remaining[0].device_mac, MAC_FMT)
    check.check("甲的心率没被串", remaining[0].heart_rate, 87)

    # 同一只手环连来两个 A：前一个让出去，不能默默丢
    parser = T10PacketParser(merge_timeout_seconds=5.0)
    parser.feed(build_response_a(2410, 90, 3652, 87, 99, 265))
    samples = parser.feed(build_response_a(2410, 91, 3653, 88, 98, 266))
    check.check("连来两个 A 时前一个被让出", len(samples), 1)
    check.check("让出的是先到的那个", samples[0].battery, 90)
    check.check("后到的仍挂着等 B", parser.flush()[0].battery, 91)

    # 三只手环的广播交错到达，各归各的
    parser = T10PacketParser()
    third = "5410260100E7"
    macs = [MAC, MAC_2, third, MAC_2, MAC]
    got = [parser.feed(build_broadcast(70 + i, 96, 3640, mac=m))[0] for i, m in enumerate(macs)]
    check.check("五条广播全部解析", len(got), 5)
    check.check("交错到达时 MAC 不串", [s.device_mac for s in got][2], "54:10:26:01:00:E7")
    check.check("心率各归各的", [s.heart_rate for s in got], [70, 71, 72, 73, 74])

    # ---------- 手环注册表 ----------
    print("\n手环注册表（可信性与轮转顺序）")
    registry = WristbandRegistry(preset_macs=[MAC], min_sightings=1)
    check.check("预置手环排在队首", registry.target_macs(), [MAC_FMT])
    check.check("预置手环没数据也留在轮转里", registry.active_macs(60), [MAC_FMT])

    record, is_new = registry.observe(MAC_2_FMT, packet_type=PACKET_BROADCAST, rssi=-61)
    check.check("同前缀的新手环被接纳", record is not None, True)
    check.check("标记为首次发现", is_new, True)
    check.check("记下信号强度", record.last_rssi, -61)
    _, is_new = registry.observe(MAC_2_FMT, packet_type=PACKET_RESPONSE_B)
    check.check("第二次不再算首次发现", is_new, False)
    check.check("样本数累加", record.sample_count, 2)
    check.check("包型累计", sorted(record.packet_types), [PACKET_BROADCAST, PACKET_RESPONSE_B])
    check.check("轮转顺序按插入序", registry.target_macs(), [MAC_FMT, MAC_2_FMT])

    record, _ = registry.observe("AA:BB:CC:00:11:22")
    check.check("陌生前缀当噪声忽略", record, None)
    check.check("噪声计入 rejected", registry.rejected.get("AA:BB:CC:00:11:22"), 1)
    check.check("噪声不进轮转", len(registry.target_macs()), 2)

    check.check("无冒号写法也能认", registry.observe(MAC_2)[0].mac_address, MAC_2_FMT)
    check.check("MAC 长度不对直接拒", registry.observe("5410")[0], None)

    capped = WristbandRegistry(max_devices=1, min_sightings=1)
    capped.observe(MAC_FMT)
    check.check("超过上限不再收人", capped.observe(MAC_2_FMT)[0], None)

    fixed = WristbandRegistry(preset_macs=[MAC], auto_discover=False)
    check.check("关掉自动发现后只认预置", fixed.observe(MAC_2_FMT)[0], None)
    check.check("预置的仍然收", fixed.observe(MAC_FMT)[0] is not None, True)

    # 串口翻位：真机抓到过 51:10:26:01:00:E7（54 翻成 51），只出现一次。
    # 载荷带 T10 特征所以过得了签名校验，只能靠"连续看到几次"把它挡在台账外
    debounced = WristbandRegistry(preset_macs=[MAC], min_sightings=3)
    check.check("预置手环第一次就收", debounced.observe(MAC_FMT)[0] is not None, True)
    flipped = "51:10:26:01:00:E7"
    check.check("坏 MAC 第一次不收", debounced.observe(flipped, signature_ok=True)[0], None)
    check.check("坏 MAC 记在候选里", debounced.pending_candidates().get(flipped), 1)
    check.check("坏 MAC 不进轮转", debounced.target_macs(), [MAC_FMT])
    debounced.observe(MAC_2_FMT)
    debounced.observe(MAC_2_FMT)
    record, is_new = debounced.observe(MAC_2_FMT)
    check.check("真手环第三次被接纳", record is not None and is_new, True)
    check.check("接纳后从候选移除", MAC_2_FMT in debounced.pending_candidates(), False)
    check.check("接纳前的样本不计数", record.sample_count, 1)

    # 同一个翻位 MAC 隔很久再出现，不能靠时间累积凑够次数
    windowed = WristbandRegistry(min_sightings=3, sighting_window_seconds=0.2)
    stale = "54:10:26:11:00:DC"
    windowed.observe(stale, signature_ok=True)
    windowed.observe(stale, signature_ok=True)
    time.sleep(0.3)
    record, _ = windowed.observe(stale, signature_ok=True)
    check.check("时间窗外的旧记录不算", record, None)
    check.check("窗口内只剩这一次", windowed.pending_candidates().get(stale), 1)
    windowed.observe(stale, signature_ok=True)
    record, is_new = windowed.observe(stale, signature_ok=True)
    check.check("窗口内连续三次才接纳", record is not None and is_new, True)

    # ---------- 采集窗口调度 ----------
    print("\n采集窗口调度（三种角色）")
    sched = _WindowScheduler(ROLE_BROADCAST)
    check.check("广播角色恒为 TYPE=4", sched.next_window()[0], TYPE_BROADCAST)
    check.check("广播角色从不下发 AT+MAC", sched.next_window()[1], "")
    check.check("广播角色配置一次就一直收", sched.next_window()[2], float("inf"))

    targets = [MAC_FMT, MAC_2_FMT, "54:10:26:01:00:E7"]
    sched = _WindowScheduler(
        ROLE_RESPONSE, target_macs_provider=lambda: targets, response_dwell_seconds=1.0
    )
    order = [sched.next_window()[1] for _ in range(4)]
    check.check("响应角色逐只轮转并回到队首", order, [*targets, MAC_FMT])

    sched = _WindowScheduler(ROLE_RESPONSE, target_macs_provider=lambda: [])
    check.check("一只都没发现时先听广播", sched.next_window()[0], TYPE_BROADCAST)

    sched = _WindowScheduler(
        ROLE_ROTATE, target_macs_provider=lambda: targets, response_dwell_seconds=1.0
    )
    windows = [sched.next_window() for _ in range(6)]
    check.check(
        "单板在广播与响应之间交替",
        [w[0] for w in windows],
        [TYPE_BROADCAST, TYPE_RESPONSE] * 3,
    )
    check.check(
        "轮转时响应窗依次换手环",
        [w[1] for w in windows if w[0] == TYPE_RESPONSE],
        targets,
    )

    return check.report()


if __name__ == "__main__":
    sys.exit(main())
