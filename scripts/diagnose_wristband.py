"""手环接收稳定性诊断。

持续采集一段时间，统计每只手环的样本速率、静默间隔、信号强度与字段覆盖率，
用来回答"数据不实时是手环的问题、信号的问题，还是协议本身就这样"。

用法:
    python scripts/diagnose_wristband.py --port COM12                 默认跑 3 分钟
    python scripts/diagnose_wristband.py --port COM12 --seconds 60    快速看一眼
    python scripts/diagnose_wristband.py --port COM12 --mac 54:10:26:01:00:DC
"""

import argparse
import statistics
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wristband_gateway.models import HealthSample  # noqa: E402
from wristband_gateway.parser import T10PacketParser  # noqa: E402
from wristband_gateway.serial_reader import SerialGatewayReader  # noqa: E402

# 平台按字段各取最近有效值，超过这个时长该字段就算不可用
FIELD_WINDOW_SECONDS = 600
# 首页体征卡默认 10 秒轮询，样本间隔超过这个值用户就会感到"不实时"
PERCEPTIBLE_GAP_SECONDS = 15
# BLE 信号强度参考线
RSSI_GOOD = -70
RSSI_WEAK = -85

VITALS = ["heart_rate", "blood_oxygen", "systolic", "diastolic", "temperature"]
VITAL_LABEL = {
    "heart_rate": "心率",
    "blood_oxygen": "血氧",
    "systolic": "收缩压",
    "diastolic": "舒张压",
    "temperature": "体温",
}


class DeviceStats:
    def __init__(self, mac: str) -> None:
        self.mac = mac
        self.samples: list[HealthSample] = []
        self.sample_times: list[float] = []
        self.packet_counts: dict[str, int] = defaultdict(int)
        self.rssi: list[int] = []
        self.field_times: dict[str, list[float]] = defaultdict(list)
        self.dropped: list[str] = []
        self.complete = 0

    def add(self, sample: HealthSample, at: float) -> None:
        self.samples.append(sample)
        self.sample_times.append(at)
        self.packet_counts[sample.packet_type] += 1
        if sample.rssi is not None:
            self.rssi.append(sample.rssi)
        if sample.is_complete():
            self.complete += 1
        self.dropped.extend(sample.dropped_fields)
        for name in VITALS:
            if getattr(sample, name) is not None:
                self.field_times[name].append(at)

    def gaps(self) -> list[float]:
        return [
            b - a for a, b in zip(self.sample_times, self.sample_times[1:], strict=False)
        ]

    def field_gaps(self, name: str) -> list[float]:
        times = self.field_times[name]
        return [b - a for a, b in zip(times, times[1:], strict=False)]


def describe_gaps(gaps: list[float]) -> str:
    if not gaps:
        return "样本不足，无法统计间隔"
    ordered = sorted(gaps)
    p50 = statistics.median(ordered)
    p90 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))]
    return f"中位 {p50:.1f}s  九成内 {p90:.1f}s  最长 {max(ordered):.1f}s"


def rssi_verdict(values: list[int]) -> tuple[str, str]:
    if not values:
        return "未知", "没有拿到信号强度"
    avg = statistics.mean(values)
    worst = min(values)
    if avg >= RSSI_GOOD:
        return "良好", f"平均 {avg:.0f} dBm，最弱 {worst} dBm"
    if avg >= RSSI_WEAK:
        return "一般", f"平均 {avg:.0f} dBm，最弱 {worst} dBm，建议缩短距离或避开遮挡"
    return "偏弱", f"平均 {avg:.0f} dBm，最弱 {worst} dBm，距离太远或有金属遮挡"


def main() -> int:
    parser_args = argparse.ArgumentParser(description="手环接收稳定性诊断")
    parser_args.add_argument("--port", default="COM12")
    parser_args.add_argument("--seconds", type=float, default=180.0)
    parser_args.add_argument("--mac", default="", help="只关注这一只手环，留空则统计全部")
    parser_args.add_argument("--merge-timeout", type=float, default=0.5)
    parser_args.add_argument(
        "--packet-type",
        type=int,
        default=5,
        choices=[4, 5],
        help="4=只收广播包，5=连接目标拉响应包",
    )
    parser_args.add_argument("--no-sos-overlay", action="store_true")
    args = parser_args.parse_args()

    parser = T10PacketParser(merge_timeout_seconds=args.merge_timeout)
    reader = SerialGatewayReader(parser)
    stats: dict[str, DeviceStats] = {}
    started = time.monotonic()
    started_at = datetime.now()
    progress_lock = threading.Lock()

    def on_sample(sample: HealthSample) -> None:
        now = time.monotonic()
        with progress_lock:
            entry = stats.setdefault(sample.device_mac, DeviceStats(sample.device_mac))
            entry.add(sample, now)

    def stop_later() -> None:
        deadline = started + args.seconds
        last_report = started
        while time.monotonic() < deadline:
            time.sleep(1)
            now = time.monotonic()
            if now - last_report >= 20:
                last_report = now
                with progress_lock:
                    total = sum(len(s.samples) for s in stats.values())
                    devices = len(stats)
                elapsed = int(now - started)
                print(
                    f"  [{elapsed:>3}s/{int(args.seconds)}s] "
                    f"已采集 {total} 条样本，发现 {devices} 只手环",
                    flush=True,
                )
        reader.stop()

    print("=" * 82)
    print(f"手环接收稳定性诊断  端口 {args.port}  时长 {int(args.seconds)} 秒")
    print("=" * 82)
    print("采集期间请保持手环佩戴、不要熄屏离开，结束后会给出结论。\n")

    threading.Thread(target=stop_later, daemon=True).start()
    reader.run(
        port=args.port,
        baudrate=115200,
        mac_filter=args.mac,
        packet_type=args.packet_type,
        enable_broadcast_sos_overlay=not args.no_sos_overlay,
        auto_configure=True,
        on_sample=on_sample,
    )

    elapsed = time.monotonic() - started
    print()
    print("=" * 82)
    print("一、采集链路")
    print("=" * 82)
    print(f"  实际采集时长   {elapsed:.0f} 秒（{started_at:%H:%M:%S} 起）")
    print(f"  串口读到的行数 {reader.stats['lines']}")
    print(f"  解析出的样本   {reader.stats['samples']}")
    print(f"  非 T10 设备被挡 {parser.rejected_foreign} 次")
    print(f"  串口重连次数   {reader.stats['reconnects']}")
    if reader.stats["last_error"]:
        print(f"  最后一次错误   {reader.stats['last_error']}")

    if not stats:
        print("\n结论：整段时间内一条手环样本都没有收到。")
        print("  逐项排查：手环是否开机并点亮屏幕、是否在采集器 5 米内、")
        print("  是否被手机 App 或另一台采集器连着（BLE 一对一，会互斥）。")
        return 1

    print()
    print("=" * 82)
    print("二、各手环概况")
    print("=" * 82)
    print(f"  {'MAC':<20} {'样本':>5} {'速率':>10} {'完整率':>8} {'信号':>6}  包型分布")
    print("  " + "-" * 78)
    for mac, entry in sorted(stats.items(), key=lambda kv: -len(kv[1].samples)):
        rate = len(entry.samples) / elapsed
        complete_pct = entry.complete / len(entry.samples) * 100
        level, _ = rssi_verdict(entry.rssi)
        packets = " ".join(f"{k}:{v}" for k, v in sorted(entry.packet_counts.items()))
        print(
            f"  {mac:<20} {len(entry.samples):>5} {rate:>7.2f}/秒 "
            f"{complete_pct:>7.0f}% {level:>6}  {packets}"
        )

    target_mac = args.mac.upper() if args.mac else max(
        stats, key=lambda m: len(stats[m].samples)
    )
    entry = stats.get(target_mac)
    if entry is None:
        print(f"\n指定的 {target_mac} 在本次采集中没有出现。")
        return 1

    print()
    print("=" * 82)
    print(f"三、重点分析 {target_mac}")
    print("=" * 82)

    gaps = entry.gaps()
    print(f"  样本间隔       {describe_gaps(gaps)}")
    long_gaps = [g for g in gaps if g > PERCEPTIBLE_GAP_SECONDS]
    if long_gaps:
        print(
            f"  超过 {PERCEPTIBLE_GAP_SECONDS} 秒的静默 {len(long_gaps)} 次，"
            f"合计 {sum(long_gaps):.0f} 秒（占时长 {sum(long_gaps) / elapsed * 100:.0f}%）"
        )
    else:
        print(f"  没有出现超过 {PERCEPTIBLE_GAP_SECONDS} 秒的静默")

    level, detail = rssi_verdict(entry.rssi)
    print(f"  信号强度       {level}，{detail}")

    print("\n  各指标的更新情况：")
    print(f"    {'指标':<8} {'出现次数':>8} {'覆盖率':>8} {'更新间隔':>28}")
    print("    " + "-" * 56)
    field_problems = []
    for name in VITALS:
        times = entry.field_times[name]
        coverage = len(times) / len(entry.samples) * 100
        gap_text = describe_gaps(entry.field_gaps(name))
        print(f"    {VITAL_LABEL[name]:<8} {len(times):>8} {coverage:>7.0f}% {gap_text:>28}")
        if not times:
            field_problems.append(f"{VITAL_LABEL[name]}整段时间没有出现过")
        else:
            worst = max(entry.field_gaps(name), default=0)
            if worst > FIELD_WINDOW_SECONDS:
                field_problems.append(
                    f"{VITAL_LABEL[name]}最长 {worst:.0f} 秒才更新一次，"
                    f"超过平台 {FIELD_WINDOW_SECONDS // 60} 分钟的有效窗口"
                )

    if entry.dropped:
        counts: dict[str, int] = defaultdict(int)
        for item in entry.dropped:
            counts[item.split("=")[0]] += 1
        print("\n  被生理区间校验丢弃的读数：")
        for name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"    {name:<16} {count} 次")
        print("    这类值多半是手环没戴稳或刚戴上还没测准，属于设备侧问题")

    print()
    print("=" * 82)
    print("四、结论")
    print("=" * 82)

    verdicts = []
    rate = len(entry.samples) / elapsed
    # 包型轮转下单只手环的期望速率约每秒 0.4~0.6 条
    if rate < 0.15:
        verdicts.append(
            f"样本速率只有 {rate:.2f} 条/秒，明显偏低。手环可能频繁休眠，"
            "或与采集器距离过远导致大量丢包"
        )
    elif rate < 0.3:
        verdicts.append(f"样本速率 {rate:.2f} 条/秒，略低于预期，偶有丢包")
    else:
        verdicts.append(f"样本速率 {rate:.2f} 条/秒，属于正常范围")

    if long_gaps:
        verdicts.append(
            f"存在 {len(long_gaps)} 次超过 {PERCEPTIBLE_GAP_SECONDS} 秒的静默，"
            f"最长 {max(long_gaps):.0f} 秒。界面在这些时段会显得没有更新"
        )

    if level == "偏弱":
        verdicts.append("信号偏弱是丢包的主要嫌疑，先把手环拿到采集器附近复测")
    elif level == "一般":
        verdicts.append("信号一般，隔墙或金属遮挡会明显加剧丢包")

    verdicts.extend(field_problems)

    if reader.stats["reconnects"]:
        verdicts.append(
            f"串口重连了 {reader.stats['reconnects']} 次，USB 线或采集器接触不良"
        )

    completeness = entry.complete / len(entry.samples) * 100
    if completeness < 5:
        verdicts.append(
            f"完整样本只占 {completeness:.0f}%。这是协议本身的特性——"
            "只有 A/B 两个响应包在 0.5 秒内合并才会产出完整样本，"
            "平台已按字段各取最近有效值，不影响使用"
        )

    for index, item in enumerate(verdicts, 1):
        print(f"  {index}. {item}")

    healthy = rate >= 0.3 and not long_gaps and level != "偏弱" and not field_problems
    print()
    print("  总体判断：" + ("手环接收正常" if healthy else "接收存在不稳定，见上述条目"))
    return 0 if healthy else 2


if __name__ == "__main__":
    sys.exit(main())
