"""串口读取：端口探测、AT 配置、包型轮转、断线重连。

AT 命令序列与轮转节奏来自现场调试结果，不要随意改动顺序或间隔。
"""

import logging
import time
from collections.abc import Callable

import serial
from serial.tools import list_ports

from .models import HealthSample
from .parser import T10PacketParser

logger = logging.getLogger("wristband.serial")

# 采集器在设备管理器里的常见标识
GATEWAY_KEYWORDS = (
    "ch9102",
    "cp210",
    "1a86:55d4",
    "10c4:ea60",
    "silicon labs",
    "wch",
)

TYPE_BROADCAST = 4
TYPE_RESPONSE = 5

# 只认一个 MAC，非目标报文直接丢；多只手环时只有一只能进系统
STRATEGY_SINGLE_TARGET = "single_target"
# 收到什么都往上交，由解析器按 MAC 分流
STRATEGY_MULTI_TARGET = "multi_target"

ROLE_SINGLE = "single"
# 常驻 AT+TYPE=4，从不下发 AT+MAC，能同时听到所有手环，也是唯一能发现未知 MAC 的角色
ROLE_BROADCAST = "broadcast"
# 轮转 AT+MAC + AT+TYPE=5 逐只拉血压/步数/电量，一次只能盯一只
ROLE_RESPONSE = "response"
# 单板时把上面两件事时间片轮转，每只手环占空比约 1/(n+1)
ROLE_ROTATE = "rotate"

# 固件没有"取消 MAC 过滤"的命令，从盯单只切回听全场只能把过滤写成全零。
# 这条未经固件文档确认，换固件要重新验证。双板方案里广播板从不设过滤，用不到它。
MAC_FILTER_RESET_COMMAND = "AT+MAC=000000000000"

# 不需要轮转的角色用这个长度当窗口，等价于"配置一次就一直收"
_WINDOW_FOREVER = float("inf")


def list_serial_ports() -> list[dict]:
    """列出串口，并标注哪些像是手环采集器。"""
    result = []
    for port in list_ports.comports():
        blob = f"{port.description} {port.hwid} {port.manufacturer or ''}".lower()
        result.append(
            {
                "device": port.device,
                "description": port.description,
                "hwid": port.hwid,
                "serial_number": port.serial_number or "",
                "likely_gateway": any(word in blob for word in GATEWAY_KEYWORDS),
            }
        )
    return result


def detect_port() -> str:
    """按关键词自动探测采集器端口。

    插了多块同型号板子时会取到第一个，生产环境务必显式指定端口。
    """
    for item in list_serial_ports():
        if item["likely_gateway"]:
            return item["device"]
    return ""


class _WindowScheduler:
    """决定接下来一段时间用什么包型、盯哪只手环。

    返回 (包型, MAC, 窗口秒数)。MAC 为空表示不下发 AT+MAC，收全场广播。
    """

    def __init__(
        self,
        role: str,
        *,
        mac_filter: str = "",
        packet_type: int = TYPE_RESPONSE,
        target_macs_provider: Callable[[], list[str]] | None = None,
        enable_broadcast_sos_overlay: bool = True,
        response_cycle_seconds: float = 1.0,
        broadcast_cycle_seconds: float = 0.5,
        response_dwell_seconds: float = 1.0,
    ) -> None:
        self.role = role
        self.mac_filter = mac_filter
        self.packet_type = packet_type
        self.target_macs_provider = target_macs_provider
        self.enable_broadcast_sos_overlay = enable_broadcast_sos_overlay
        self.response_cycle_seconds = response_cycle_seconds
        self.broadcast_cycle_seconds = broadcast_cycle_seconds
        self.response_dwell_seconds = response_dwell_seconds
        self._cursor = 0
        self._want_broadcast = True
        self._first = True

    def _targets(self) -> list[str]:
        return list(self.target_macs_provider()) if self.target_macs_provider else []

    def next_window(self) -> tuple[int, str, float]:
        if self.role == ROLE_BROADCAST:
            return TYPE_BROADCAST, "", _WINDOW_FOREVER

        if self.role == ROLE_SINGLE:
            if not self.enable_broadcast_sos_overlay:
                return self.packet_type, self.mac_filter, _WINDOW_FOREVER
            if self._first:
                self._first = False
                use_response = self.packet_type == TYPE_RESPONSE
            else:
                use_response = self._want_broadcast
            self._want_broadcast = not use_response
            if use_response:
                return TYPE_RESPONSE, self.mac_filter, self.response_cycle_seconds
            return TYPE_BROADCAST, self.mac_filter, self.broadcast_cycle_seconds

        targets = self._targets()
        # 一只手环都还没发现时只能先听广播——AT+TYPE=5 要先知道 MAC 才能连
        if not targets:
            return TYPE_BROADCAST, "", self.broadcast_cycle_seconds

        if self.role == ROLE_ROTATE and self._want_broadcast:
            self._want_broadcast = False
            return TYPE_BROADCAST, "", self.broadcast_cycle_seconds

        self._want_broadcast = True
        mac = targets[self._cursor % len(targets)]
        self._cursor = (self._cursor + 1) % len(targets)
        return TYPE_RESPONSE, mac, self.response_dwell_seconds


class SerialGatewayReader:
    """阻塞式读取器，内部无限重连；放到线程里跑。"""

    def __init__(self, parser: T10PacketParser | None = None) -> None:
        self.parser = parser or T10PacketParser()
        self.running = False
        self.stats = {
            "lines": 0,
            "samples": 0,
            "complete_samples": 0,
            "filtered_out": 0,
            "reconnects": 0,
            "last_line_at": None,
            "last_sample_at": None,
            "port": "",
            "connected": False,
            "last_error": "",
            "strategy": STRATEGY_SINGLE_TARGET,
            "role": ROLE_SINGLE,
            "current_target": "",
            "current_packet_type": 0,
        }
        # MAC -> 出现次数，用于发现附近有哪些手环
        self.seen_devices: dict[str, int] = {}
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    # ---------- AT 命令 ----------

    def _send_at(self, conn: serial.Serial, command: str, delay: float) -> None:
        conn.write(f"{command}\r\n".encode())
        conn.flush()
        time.sleep(delay)

    def _apply_window(
        self,
        conn: serial.Serial,
        mac_filter: str,
        packet_type: int,
        disable_uuid_output: bool,
        command_delay_seconds: float,
        clear_mac_filter: bool = False,
    ) -> None:
        """SCANSTOP → UUID → MAC → TYPE → SCANSTART，顺序不能乱。

        SCANSTART 之后再发 MAC 不生效，必须先 SCANSTOP。
        """
        self._send_at(conn, "AT+SCANSTOP", command_delay_seconds)
        if disable_uuid_output:
            self._send_at(conn, "AT+UUID=NO", command_delay_seconds)
        if mac_filter:
            plain = mac_filter.replace(":", "").replace("-", "").upper()
            self._send_at(conn, f"AT+MAC={plain}", command_delay_seconds)
        elif clear_mac_filter:
            self._send_at(conn, MAC_FILTER_RESET_COMMAND, command_delay_seconds)
        self._send_at(conn, f"AT+TYPE={packet_type}", command_delay_seconds)
        self._send_at(conn, "AT+SCANSTART", command_delay_seconds)

    # 旧名字，外部脚本还在用
    _configure_single_target = _apply_window

    # ---------- 主循环 ----------

    def run(
        self,
        port: str | None = None,
        baudrate: int = 115200,
        mac_filter: str = "",
        packet_type: int = TYPE_RESPONSE,
        enable_broadcast_sos_overlay: bool = True,
        response_cycle_seconds: float = 1.0,
        broadcast_cycle_seconds: float = 0.5,
        command_delay_seconds: float = 0.05,
        auto_configure: bool = True,
        disable_uuid_output: bool = True,
        reconnect_delay_seconds: float = 2.0,
        on_sample: Callable[[HealthSample], None] | None = None,
        on_line: Callable[[str], None] | None = None,
        target_mac_provider: Callable[[], str] | None = None,
        accept_macs: set[str] | None = None,
        collection_strategy: str = STRATEGY_SINGLE_TARGET,
        collector_role: str = "",
        target_macs_provider: Callable[[], list[str]] | None = None,
        response_dwell_seconds: float = 1.0,
    ) -> None:
        self.running = True
        self._stop_requested = False
        if collection_strategy == STRATEGY_MULTI_TARGET:
            role = collector_role or ROLE_ROTATE
            if accept_macs:
                # 多手环模式下留着白名单，等于把好不容易收到的另外两只又丢掉一次
                logger.warning("multi_target 策略忽略 MAC 白名单 %s", sorted(accept_macs))
                accept_macs = None
        else:
            role = ROLE_SINGLE
        self.stats["strategy"] = collection_strategy
        self.stats["role"] = role

        while not self._stop_requested:
            resolved = port or detect_port()
            if not resolved:
                self.stats["last_error"] = "没有找到采集器端口"
                logger.warning("未探测到采集器端口，%.1fs 后重试", reconnect_delay_seconds)
                time.sleep(reconnect_delay_seconds)
                continue

            self.stats["port"] = resolved
            try:
                with serial.Serial(resolved, baudrate, timeout=0.2) as conn:
                    self.stats["connected"] = True
                    self.stats["last_error"] = ""
                    logger.info("串口已打开 %s @ %d", resolved, baudrate)
                    self._read_loop(
                        conn,
                        mac_filter=mac_filter,
                        packet_type=packet_type,
                        enable_broadcast_sos_overlay=enable_broadcast_sos_overlay,
                        response_cycle_seconds=response_cycle_seconds,
                        broadcast_cycle_seconds=broadcast_cycle_seconds,
                        command_delay_seconds=command_delay_seconds,
                        auto_configure=auto_configure,
                        disable_uuid_output=disable_uuid_output,
                        on_sample=on_sample,
                        on_line=on_line,
                        target_mac_provider=target_mac_provider,
                        accept_macs=accept_macs,
                        role=role,
                        target_macs_provider=target_macs_provider,
                        response_dwell_seconds=response_dwell_seconds,
                    )
            except serial.SerialException as exc:
                self.stats["connected"] = False
                self.stats["reconnects"] += 1
                self.stats["last_error"] = str(exc)
                # 串口被占用是最常见的原因：后端、串口助手或未释放的旧进程
                logger.warning("串口异常（%s），%.1fs 后重连", exc, reconnect_delay_seconds)
                time.sleep(reconnect_delay_seconds)
            except Exception as exc:
                self.stats["connected"] = False
                self.stats["last_error"] = str(exc)
                logger.exception("读取循环异常，%.1fs 后重连", reconnect_delay_seconds)
                time.sleep(reconnect_delay_seconds)

        self.stats["connected"] = False
        self.running = False
        logger.info("读取器已停止")

    def _read_loop(
        self,
        conn: serial.Serial,
        *,
        mac_filter: str,
        packet_type: int,
        enable_broadcast_sos_overlay: bool,
        response_cycle_seconds: float,
        broadcast_cycle_seconds: float,
        command_delay_seconds: float,
        auto_configure: bool,
        disable_uuid_output: bool,
        on_sample: Callable[[HealthSample], None] | None,
        on_line: Callable[[str], None] | None,
        target_mac_provider: Callable[[], str] | None,
        accept_macs: set[str] | None = None,
        role: str = ROLE_SINGLE,
        target_macs_provider: Callable[[], list[str]] | None = None,
        response_dwell_seconds: float = 1.0,
    ) -> None:
        if role == ROLE_SINGLE and target_mac_provider:
            latest = target_mac_provider()
            if latest:
                mac_filter = latest

        scheduler = _WindowScheduler(
            role,
            mac_filter=mac_filter,
            packet_type=packet_type,
            target_macs_provider=target_macs_provider,
            enable_broadcast_sos_overlay=enable_broadcast_sos_overlay,
            response_cycle_seconds=response_cycle_seconds,
            broadcast_cycle_seconds=broadcast_cycle_seconds,
            response_dwell_seconds=response_dwell_seconds,
        )
        applied: tuple[int, str] | None = None
        mac_filter_active = False
        window_ends_at = 0.0

        while not self._stop_requested:
            if auto_configure and time.monotonic() >= window_ends_at:
                if role == ROLE_SINGLE and target_mac_provider:
                    latest = target_mac_provider()
                    if latest and latest != scheduler.mac_filter:
                        logger.info("切换目标手环 %s", latest)
                        scheduler.mac_filter = latest
                current_type, current_mac, duration = scheduler.next_window()
                window_ends_at = time.monotonic() + duration
                if (current_type, current_mac) != applied:
                    self._apply_window(
                        conn,
                        current_mac,
                        current_type,
                        disable_uuid_output,
                        command_delay_seconds,
                        # AT+MAC 只能设不能清，切回听全场时必须显式归零
                        clear_mac_filter=mac_filter_active and not current_mac,
                    )
                    applied = (current_type, current_mac)
                    mac_filter_active = bool(current_mac)
                    self.stats["current_target"] = current_mac
                    self.stats["current_packet_type"] = current_type

            raw = conn.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="ignore").strip()
            if not line:
                continue

            self.stats["lines"] += 1
            self.stats["last_line_at"] = time.time()
            if on_line:
                on_line(line)

            for sample in self.parser.feed(line):
                if sample.device_mac:
                    self.seen_devices[sample.device_mac] = (
                        self.seen_devices.get(sample.device_mac, 0) + 1
                    )
                # 采集器的 AT+MAC 过滤实测不总是生效，单目标模式下这里再挡一道。
                # 注意包已经收到、也已经计进 seen_devices，只是不往上交——
                # 现场"另外两只手环永远离线"就是这一行造成的，多手环模式禁用它
                if accept_macs and sample.device_mac not in accept_macs:
                    self.stats["filtered_out"] += 1
                    continue

                self.stats["samples"] += 1
                self.stats["last_sample_at"] = time.time()
                if sample.is_complete():
                    self.stats["complete_samples"] += 1
                if on_sample:
                    on_sample(sample)
