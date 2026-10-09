"""手环采集网关命令行入口。

    python -m wristband_gateway.main ports                    列出串口
    python -m wristband_gateway.main raw --port COM12 --scan  看原始 HEX 流
    python -m wristband_gateway.main run --port COM12         单只手环采集并上报
    python -m wristband_gateway.main multi --broadcast-port COM12 --response-port COM13
                                                              多只手环采集并上报

现场有多只手环时用 multi。run 只认一个 MAC，其余手环的包会在上报前被白名单
丢掉——包收到了、也解析成样本了，就是不往上交，界面上看起来和"手环坏了"一模一样。
"""

import argparse
import logging
import signal
import sys
import threading
import time

from .device_discovery import DEFAULT_MAC_PREFIXES, WristbandRegistry, normalize_mac
from .models import HealthSample
from .parser import T10PacketParser
from .publisher import SamplePublisher, mac_to_device_id, merge_reader_stats
from .serial_reader import (
    ROLE_BROADCAST,
    ROLE_RESPONSE,
    ROLE_ROTATE,
    STRATEGY_MULTI_TARGET,
    SerialGatewayReader,
    list_serial_ports,
)

DEFAULT_MAC = "54:10:26:01:00:DC"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("wristband")


def cmd_ports(_args) -> int:
    ports = list_serial_ports()
    if not ports:
        print("没有找到任何串口。检查 USB 是否插好、驱动是否安装。")
        return 1
    print(f"{'端口':<8} {'像采集器':<10} {'描述':<44} 硬件 ID")
    print("-" * 108)
    for item in ports:
        flag = "是" if item["likely_gateway"] else "否"
        print(f"{item['device']:<8} {flag:<10} {item['description'][:42]:<44} {item['hwid']}")
    print()
    print("插了多块同型号采集器时，务必用 --port 显式指定，自动探测会取第一个。")
    return 0


def cmd_raw(args) -> int:
    """只看原始行，用于确认采集器活着、手环在发数据。"""
    parser = T10PacketParser(merge_timeout_seconds=args.merge_timeout)
    reader = SerialGatewayReader(parser)

    def on_line(line: str) -> None:
        print(f"  < {line}")

    def on_sample(sample: HealthSample) -> None:
        print(f"  => {sample.summary()}")

    _install_stop(reader)
    print("按 Ctrl+C 停止。原始行以 < 开头，解析出的样本以 => 开头。")
    reader.run(
        port=args.port,
        baudrate=args.baudrate,
        mac_filter=args.mac,
        packet_type=args.packet_type,
        enable_broadcast_sos_overlay=not args.no_sos_overlay,
        auto_configure=args.scan,
        on_line=on_line,
        on_sample=on_sample,
    )
    return 0


def cmd_discover(args) -> int:
    """不做 MAC 过滤，扫一段时间看附近有哪些手环在广播。"""
    parser = T10PacketParser(merge_timeout_seconds=args.merge_timeout)
    reader = SerialGatewayReader(parser)
    found: dict[str, dict] = {}

    def on_sample(sample: HealthSample) -> None:
        entry = found.setdefault(
            sample.device_mac,
            {"count": 0, "packets": set(), "last": None},
        )
        entry["count"] += 1
        entry["packets"].add(sample.packet_type)
        entry["last"] = sample

    def stop_after() -> None:
        time.sleep(args.seconds)
        reader.stop()

    threading.Thread(target=stop_after, daemon=True).start()
    _install_stop(reader)
    print(f"扫描 {args.seconds} 秒，期间请按亮手环屏幕让它开始广播…")
    reader.run(
        port=args.port,
        baudrate=args.baudrate,
        mac_filter="",
        packet_type=args.packet_type,
        enable_broadcast_sos_overlay=not args.no_sos_overlay,
        auto_configure=True,
        on_sample=on_sample,
    )

    print()
    if not found:
        print("没有发现任何手环。请确认手环已开机、屏幕点亮、距离采集器 5 米内。")
        return 1
    print(f"共发现 {len(found)} 个设备：\n")
    print(f"{'MAC':<20} {'样本数':<8} {'包型':<28} 最近一条")
    print("-" * 104)
    for mac, entry in sorted(found.items(), key=lambda kv: -kv[1]["count"]):
        packets = ",".join(sorted(entry["packets"]))
        last = entry["last"]
        vitals = " ".join(f"{k}={v}" for k, v in last.vitals().items()) or "无有效字段"
        print(f"{mac:<20} {entry['count']:<8} {packets:<28} {vitals}")
    print()
    print("挑出属于老人的那只手环 MAC，用 --mac 传给 run 子命令。")
    return 0


def cmd_run(args) -> int:
    """正式采集：解析后按平台约定的 MQTT 主题上报。"""
    parser = T10PacketParser(
        merge_timeout_seconds=args.merge_timeout,
        fallback_device_mac=args.mac,
    )
    reader = SerialGatewayReader(parser)
    publisher = SamplePublisher(
        host=args.mqtt_host,
        port=args.mqtt_port,
        topic_prefix=args.topic_prefix,
        gateway_id=args.gateway_id,
        partial_flush_seconds=args.flush_interval,
    )
    publisher.start()

    if args.mac:
        print(f"目标手环 {args.mac} → 设备编号 {mac_to_device_id(args.mac)}（即 MAC 去掉冒号）")
        print("首次上报后请到后台「设置 → 设备管理」把它绑定到对应老人，否则数据不会入库。")
    if args.mac and not args.accept_all:
        print("注意：只上报这一只手环。其余手环的包会收到、会解析，但在上报前被白名单丢掉，")
        print("      界面上表现为「从未上报」。现场有多只手环请改用 multi 子命令。")

    def on_sample(sample: HealthSample) -> None:
        if args.verbose or sample.is_complete():
            print(f"  {sample.summary()}")
        publisher.handle_sample(sample)

    # 心跳单独一个线程，串口没数据时也要让平台知道网关还活着
    stop_event = threading.Event()

    def heartbeat_loop() -> None:
        while not stop_event.is_set():
            publisher.maybe_heartbeat(reader.stats)
            stop_event.wait(2)

    heartbeat = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat.start()

    _install_stop(reader, extra=lambda: (stop_event.set(), publisher.stop()))
    print("按 Ctrl+C 停止。只有标记 [完整] 的样本同时含心率/血氧/体温/血压。")
    accept = {args.mac.upper()} if args.mac and not args.accept_all else None
    # 默认不下发 AT+MAC：实测它会让采集器把时间花在建连上，
    # 串口吞吐掉到七分之一，而且并没有真的挡住无关设备。软件过滤更可靠。
    hardware_filter = args.mac if args.hardware_filter else ""
    if args.mac and not args.hardware_filter:
        print("MAC 过滤在软件侧完成，不下发 AT+MAC（避免采集器吞吐骤降）")
    try:
        reader.run(
            port=args.port,
            baudrate=args.baudrate,
            mac_filter=hardware_filter,
            packet_type=args.packet_type,
            enable_broadcast_sos_overlay=not args.no_sos_overlay,
            response_cycle_seconds=args.response_cycle,
            broadcast_cycle_seconds=args.broadcast_cycle,
            on_sample=on_sample,
            accept_macs=accept,
        )
    finally:
        stop_event.set()
        publisher.stop()
        print(
            f"\n累计：读取 {reader.stats['lines']} 行，"
            f"解析 {reader.stats['samples']} 条样本"
            f"（完整 {reader.stats['complete_samples']} 条），"
            f"上报 {publisher.published} 条"
        )
    return 0


def cmd_multi(args) -> int:
    """多手环采集：不做 MAC 白名单，由注册表按可信前缀分流。

    双板分工时广播板常驻 AT+TYPE=4 收全场心率血氧，响应板轮转 AT+MAC 逐只拉血压；
    只有一块板时退化成时间片轮转，每只手环的采样率降到约 1/(n+1)。
    """
    presets = [normalize_mac(m) for m in args.target_macs.split(",") if m.strip()]
    presets = [m for m in presets if m]
    registry = WristbandRegistry(
        preset_macs=presets,
        mac_prefixes=tuple(p.strip() for p in args.mac_prefixes.split(",") if p.strip()),
        auto_discover=not args.no_auto_discover,
    )
    # 多手环时绝不能设兜底 MAC：认不出 MAC 的包会被统统记到那一只名下
    parser = T10PacketParser(merge_timeout_seconds=args.merge_timeout)
    publisher = SamplePublisher(
        host=args.mqtt_host,
        port=args.mqtt_port,
        topic_prefix=args.topic_prefix,
        gateway_id=args.gateway_id,
        partial_flush_seconds=args.flush_interval,
    )
    publisher.start()

    def on_sample(sample: HealthSample) -> None:
        record, is_new = registry.observe(
            sample.device_mac,
            packet_type=sample.packet_type,
            signature_ok=parser.require_t10_signature,
            rssi=sample.rssi,
        )
        if record is None:
            return
        if is_new:
            print(
                f"  发现手环 {record.mac_address} → 设备编号 "
                f"{mac_to_device_id(record.mac_address)}"
            )
            print("    自动登记后是未绑定状态，需要到「设置 → 设备管理」绑定到老人才会入库。")
        if args.verbose or sample.is_complete():
            print(f"  {sample.summary()}")
        publisher.handle_sample(sample)

    dual = bool(args.response_port) and args.response_port != args.broadcast_port
    plan: list[tuple[str, str]] = []
    if dual:
        plan = [(ROLE_BROADCAST, args.broadcast_port), (ROLE_RESPONSE, args.response_port)]
    else:
        plan = [(ROLE_ROTATE, args.broadcast_port or args.port)]

    readers: list[tuple[str, SerialGatewayReader]] = []
    threads: list[threading.Thread] = []
    for role, port in plan:
        reader = SerialGatewayReader(parser)
        readers.append((role, reader))
        thread = threading.Thread(
            target=lambda r=reader, ro=role, p=port: r.run(
                port=p,
                baudrate=args.baudrate,
                collection_strategy=STRATEGY_MULTI_TARGET,
                collector_role=ro,
                target_macs_provider=lambda: registry.active_macs(args.stale_seconds),
                response_dwell_seconds=args.response_dwell,
                broadcast_cycle_seconds=args.broadcast_cycle,
                on_sample=on_sample,
            ),
            name=f"collector-{role}",
            daemon=True,
        )
        threads.append(thread)

    stop_event = threading.Event()

    def heartbeat_loop() -> None:
        while not stop_event.is_set():
            publisher.maybe_heartbeat(
                merge_reader_stats([(role, reader.stats) for role, reader in readers]),
                wristbands=registry.snapshot(),
            )
            stop_event.wait(2)

    def report_loop() -> None:
        while not stop_event.wait(args.report_interval):
            _print_wristband_table(registry)

    def stop_all() -> None:
        stop_event.set()
        for _, reader in readers:
            reader.stop()
        publisher.stop()

    def handler(*_args):
        print("\n正在停止…")
        stop_all()

    signal.signal(signal.SIGINT, handler)
    try:
        signal.signal(signal.SIGTERM, handler)
    except (AttributeError, ValueError):
        pass

    if dual:
        print(f"双板分工：广播板 {args.broadcast_port}（全部手环）+ 响应板 "
              f"{args.response_port}（轮转拉血压）")
    else:
        print(f"单板轮转：{plan[0][1] or '自动探测'}，每只手环采样率约 1/(n+1)，能凑出两块板就用两块")
    if presets:
        print(f"预置手环：{'、'.join(presets)}（排在轮转队首，永不被拒）")
    print(f"可信 MAC 前缀：{args.mac_prefixes}；不做 MAC 白名单，所有可信手环都会上报")
    print("按 Ctrl+C 停止。\n")

    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=report_loop, daemon=True).start()
    for thread in threads:
        thread.start()
    try:
        while any(thread.is_alive() for thread in threads):
            time.sleep(0.3)
    except KeyboardInterrupt:
        stop_all()
    finally:
        stop_all()
        for sample in parser.flush():
            publisher.handle_sample(sample)
        publisher.flush_all()
        _print_wristband_table(registry)
        if registry.rejected:
            print("\n被当作噪声忽略的设备（MAC 前缀不可信）：")
            for mac, count in sorted(registry.rejected.items(), key=lambda kv: -kv[1])[:10]:
                print(f"  {mac:<20} {count} 次")
            print("  确实是手环的话，加进 --target-macs 或 --mac-prefixes。")
    return 0


def _print_wristband_table(registry: WristbandRegistry) -> None:
    """按手环列采集情况。整体时间戳会被还在发数据的那只盖住，必须逐只看。"""
    rows = registry.snapshot()
    if not rows:
        print("\n还没有发现任何手环。确认手环已开机、屏幕点亮、距离采集器 5 米内。")
        return
    print(f"\n{'手环 MAC':<20} {'样本数':<8} {'信号':<9} {'包型':<30} {'距上次':<10} 备注")
    print("-" * 100)
    for row in rows:
        since = row["seconds_since_sample"]
        since_text = "从未" if since is None else f"{since:.0f}s"
        rssi = row.get("last_rssi")
        rssi_text = "--" if rssi is None else f"{rssi}dBm"
        note = "预置" if row["preset"] else ""
        if since is None:
            note = (note + " 未收到任何包").strip()
        elif since > 120:
            note = (note + " 疑似断流").strip()
        elif rssi is not None and rssi < -85:
            note = (note + " 信号弱，离采集器远或有遮挡").strip()
        packets = ",".join(row["packet_types"]) or "--"
        print(f"{row['mac_address']:<20} {row['sample_count']:<8} {rssi_text:<9} {packets:<30} "
              f"{since_text:<10} {note}")
    pending = registry.pending_candidates()
    if pending:
        shown = ", ".join(f"{mac}×{count}" for mac, count in sorted(pending.items()))
        print(f"  未达准入次数的 MAC（多为串口翻位坏行）：{shown}")


def _install_stop(reader: SerialGatewayReader, extra=None) -> None:
    def handler(*_args):
        print("\n正在停止…")
        reader.stop()
        if extra:
            extra()

    signal.signal(signal.SIGINT, handler)
    try:
        signal.signal(signal.SIGTERM, handler)
    except (AttributeError, ValueError):
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="T10 手环采集网关")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ports", help="列出串口").set_defaults(func=cmd_ports)

    def add_serial_args(p):
        p.add_argument("--port", default=None, help="串口号，留空自动探测")
        p.add_argument("--baudrate", type=int, default=115200)
        p.add_argument("--mac", default=DEFAULT_MAC, help="目标手环 MAC")
        p.add_argument("--packet-type", type=int, default=5, choices=[4, 5])
        p.add_argument("--merge-timeout", type=float, default=0.5, help="A/B 包合并窗口秒数")
        p.add_argument("--no-sos-overlay", action="store_true", help="关闭包型轮转，将收不到 SOS")

    raw = sub.add_parser("raw", help="查看原始 HEX 流")
    add_serial_args(raw)
    raw.add_argument("--scan", action="store_true", help="打开时自动发 AT 配置命令")
    raw.set_defaults(func=cmd_raw)

    discover = sub.add_parser("discover", help="扫描附近有哪些手环")
    add_serial_args(discover)
    discover.add_argument("--seconds", type=float, default=20.0, help="扫描时长")
    discover.set_defaults(func=cmd_discover)

    def add_publish_args(p):
        p.add_argument("--mqtt-host", default="localhost")
        p.add_argument("--mqtt-port", type=int, default=1883)
        p.add_argument("--topic-prefix", default="aiot")
        p.add_argument("--gateway-id", default="wristband-gw-1")
        p.add_argument("--flush-interval", type=float, default=5.0, help="部分样本合并上报间隔")
        p.add_argument("--verbose", action="store_true", help="连部分样本也打印")

    run = sub.add_parser("run", help="单只手环采集并上报平台")
    add_serial_args(run)
    add_publish_args(run)
    run.add_argument("--response-cycle", type=float, default=1.0)
    run.add_argument("--broadcast-cycle", type=float, default=0.5)
    run.add_argument("--accept-all", action="store_true", help="不做 MAC 过滤，上报所有手环")
    run.add_argument(
        "--hardware-filter",
        action="store_true",
        help="额外下发 AT+MAC 让采集器侧过滤（实测会大幅降低吞吐，一般不用）",
    )
    run.set_defaults(func=cmd_run)

    multi = sub.add_parser("multi", help="多只手环采集并上报平台（推荐）")
    add_publish_args(multi)
    multi.add_argument("--port", default=None, help="只有一块板时用这个，等同 --broadcast-port")
    multi.add_argument("--broadcast-port", default=None, help="广播板端口，收全部手环的心率血氧")
    multi.add_argument(
        "--response-port", default=None, help="响应板端口，轮转拉血压；留空则单板轮转"
    )
    multi.add_argument("--baudrate", type=int, default=115200)
    multi.add_argument("--merge-timeout", type=float, default=0.5, help="A/B 包合并窗口秒数")
    multi.add_argument("--response-dwell", type=float, default=1.0, help="每只手环停留秒数")
    multi.add_argument("--broadcast-cycle", type=float, default=0.5, help="广播窗口秒数")
    multi.add_argument(
        "--target-macs", default="", help="预置手环 MAC，逗号分隔；排在轮转队首且永不被拒"
    )
    multi.add_argument(
        "--mac-prefixes",
        default=",".join(DEFAULT_MAC_PREFIXES),
        help="可信 MAC 前缀，把其他 BLE 设备挡在台账外",
    )
    multi.add_argument("--no-auto-discover", action="store_true", help="只采 --target-macs 里的手环")
    multi.add_argument(
        "--stale-seconds", type=float, default=120.0, help="超过这么久没数据就不再轮转它"
    )
    multi.add_argument("--report-interval", type=float, default=30.0, help="逐手环统计打印间隔")
    multi.set_defaults(func=cmd_multi)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
