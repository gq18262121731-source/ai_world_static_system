"""采集网关的在线状态登记。

网关不是绑定到某位老人的设备，而是一条数据通道（串口 -> MQTT），
它的存活情况单独记录：串口断了但网关进程还在，和网关整个挂掉，处理方式不一样。

状态是易失的，后端重启后由网关的下一次心跳重新填充（默认 10 秒一次）。
"""

from datetime import datetime

from app.core.logging import get_logger

logger = get_logger(__name__)

# 超过这个时间没收到心跳就认为网关掉线
GATEWAY_OFFLINE_SECONDS = 45
# 网关在线但这么久没有新样本，说明串口通道有问题
SAMPLE_STALL_SECONDS = 120
# 单只手环这么久没数据就算它断了。整体时间戳只要有一只在发就一直在刷新，
# 三只里断一只从整体上完全看不出来，必须逐只判断。
WRISTBAND_STALL_SECONDS = 180


class GatewayRegistry:
    def __init__(self) -> None:
        self._items: dict[str, dict] = {}

    def update(self, gateway_id: str, payload: dict) -> None:
        self._items[gateway_id] = {
            **payload,
            "gateway_id": gateway_id,
            "received_at": datetime.now(),
        }

    def snapshot(self) -> list[dict]:
        now = datetime.now()
        result = []
        for item in self._items.values():
            since_heartbeat = (now - item["received_at"]).total_seconds()
            online = since_heartbeat <= GATEWAY_OFFLINE_SECONDS
            since_sample = item.get("seconds_since_sample")
            issues = []
            if not online:
                issues.append(
                    f"已有 {int(since_heartbeat)} 秒没有心跳，网关进程可能已停止"
                )
            elif not item.get("serial_connected"):
                issues.append(
                    f"串口 {item.get('serial_port') or '未知'} 未连接："
                    f"{item.get('last_error') or '端口可能被其它程序占用'}"
                )
            elif since_sample is not None and since_sample > SAMPLE_STALL_SECONDS:
                issues.append(
                    f"串口正常但已有 {int(since_sample)} 秒没有新样本，"
                    "手环可能没电、休眠或超出蓝牙范围"
                )

            wristbands = _wristband_view(item.get("wristbands") or [])
            if online:
                issues.extend(_wristband_issues(wristbands))
            filtered_out = item.get("filtered_out", 0) or 0
            if online and filtered_out:
                issues.append(
                    f"网关已丢弃 {filtered_out} 条非目标手环的样本："
                    "启动参数带了 MAC 白名单，其余手环收得到但不会上报"
                )

            result.append(
                {
                    "gateway_id": item["gateway_id"],
                    "serial_port": item.get("serial_port", ""),
                    "serial_connected": bool(item.get("serial_connected")),
                    "online": online,
                    "seconds_since_heartbeat": round(since_heartbeat, 1),
                    "seconds_since_sample": since_sample,
                    "samples": item.get("samples", 0),
                    "complete_samples": item.get("complete_samples", 0),
                    "published": item.get("published", 0),
                    "reconnects": item.get("reconnects", 0),
                    "filtered_out": filtered_out,
                    "strategy": item.get("strategy", ""),
                    "collectors": item.get("collectors") or [],
                    "wristbands": wristbands,
                    "last_error": item.get("last_error", ""),
                    "issues": issues,
                }
            )
        return sorted(result, key=lambda item: item["gateway_id"])

    @property
    def healthy(self) -> bool:
        items = self.snapshot()
        return bool(items) and all(not item["issues"] for item in items)


def _wristband_view(rows: list[dict]) -> list[dict]:
    """给每只手环补一个人能直接看懂的状态。"""
    result = []
    for row in rows:
        since = row.get("seconds_since_sample")
        if since is None:
            state = "never"
        elif since > WRISTBAND_STALL_SECONDS:
            state = "stalled"
        else:
            state = "streaming"
        result.append({**row, "state": state})
    return sorted(result, key=lambda item: item.get("mac_address", ""))


def _wristband_issues(wristbands: list[dict]) -> list[str]:
    if not wristbands:
        return ["网关还没有发现任何手环，确认手环已开机且在采集器蓝牙范围内"]
    issues = []
    never = [item["mac_address"] for item in wristbands if item["state"] == "never"]
    stalled = [item["mac_address"] for item in wristbands if item["state"] == "stalled"]
    if never:
        issues.append(f"手环 {'、'.join(never)} 从未收到过数据，检查电量与距离")
    if stalled:
        issues.append(
            f"手环 {'、'.join(stalled)} 已超过 {WRISTBAND_STALL_SECONDS} 秒没有新数据"
        )
    return issues


gateway_registry = GatewayRegistry()
