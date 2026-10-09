import asyncio
import re
import time
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.events import hub
from app.core.logging import get_logger
from app.iot import commands, probe
from app.iot.gateways import gateway_registry
from app.iot.mqtt_client import mqtt_bridge
from app.models import Device, Elder, HealthRecord
from app.models.enums import DeviceStatus, DeviceType

logger = get_logger(__name__)

SIMULATOR_ONLINE_OVERRIDE_KEY = "simulator_online_override"


# 手环用自己的 MAC 当设备编号，人工登记时可能写成 54:10:26:01:00:DC 或带横杠，
# 统一规范成 12 位大写十六进制，避免同一块设备出现两条对不上的记录
_MAC_LIKE = re.compile(r"^[0-9A-Fa-f]{2}([:\-]?)(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}$")


def normalize_device_id(device_id: str) -> str:
    text = (device_id or "").strip()
    if _MAC_LIKE.match(text):
        return text.replace(":", "").replace("-", "").upper()
    return text


async def get_by_device_id(db: AsyncSession, device_id: str) -> Device | None:
    result = await db.execute(select(Device).where(Device.device_id == device_id))
    return result.scalar_one_or_none()


async def list_devices(
    db: AsyncSession, elder_id: int | None = None, device_type: str | None = None
) -> list[Device]:
    stmt = select(Device).order_by(Device.id)
    if elder_id is not None:
        stmt = stmt.where(Device.elder_id == elder_id)
    if device_type:
        stmt = stmt.where(Device.device_type == device_type)
    return list((await db.execute(stmt)).scalars().all())


async def get_elder_device(
    db: AsyncSession, elder_id: int, device_type: str
) -> Device | None:
    result = await db.execute(
        select(Device)
        .where(Device.elder_id == elder_id, Device.device_type == device_type)
        .order_by(Device.id)
    )
    return result.scalars().first()


async def ensure_device(
    db: AsyncSession,
    device_id: str,
    device_type: str,
    elder_id: int | None = None,
    name: str = "",
) -> Device:
    """设备首次上报时自动注册, 避免真机接入前必须先建档。"""
    device = await get_by_device_id(db, device_id)
    if device:
        return device
    device = Device(
        device_id=device_id,
        device_type=device_type,
        name=name or device_id,
        elder_id=elder_id,
        status=DeviceStatus.ONLINE,
        last_seen_at=datetime.now(),
    )
    db.add(device)
    await db.flush()
    logger.info("自动注册设备 %s (%s)", device_id, device_type)
    return device


async def touch(
    db: AsyncSession,
    device: Device,
    battery: int | None = None,
    lat: float | None = None,
    lng: float | None = None,
    extra: dict | None = None,
) -> None:
    """刷新心跳与最新状态。"""
    device.last_seen_at = datetime.now()
    was_offline = device.status != DeviceStatus.ONLINE
    online_override = (device.extra or {}).get(SIMULATOR_ONLINE_OVERRIDE_KEY)
    device.status = (
        DeviceStatus.ONLINE
        if online_override is not False
        else DeviceStatus.OFFLINE
    )
    if battery is not None:
        device.battery = int(battery)
    if lat is not None and lng is not None:
        device.last_lat = lat
        device.last_lng = lng
    if extra:
        merged = dict(device.extra or {})
        merged.update(extra)
        device.extra = merged
    await db.flush()
    if was_offline and device.status == DeviceStatus.ONLINE:
        await hub.broadcast(
            "device_status",
            {"device_id": device.device_id, "status": DeviceStatus.ONLINE},
            elder_id=device.elder_id,
        )


async def mark_offline_devices(db: AsyncSession) -> list[Device]:
    """心跳超时的设备判定为离线。"""
    deadline = datetime.now() - timedelta(seconds=settings.device_offline_seconds)
    result = await db.execute(
        select(Device).where(
            Device.status == DeviceStatus.ONLINE,
            Device.last_seen_at.is_not(None),
            Device.last_seen_at < deadline,
        )
    )
    devices = [
        device
        for device in result.scalars().all()
        if (device.extra or {}).get(SIMULATOR_ONLINE_OVERRIDE_KEY) is not True
    ]
    if not devices:
        return []
    await db.execute(
        update(Device)
        .where(Device.id.in_([d.id for d in devices]))
        .values(status=DeviceStatus.OFFLINE)
    )
    await db.flush()
    for device in devices:
        await hub.broadcast(
            "device_status",
            {"device_id": device.device_id, "status": DeviceStatus.OFFLINE},
            elder_id=device.elder_id,
        )
    return devices


async def resolve_elder_id(
    db: AsyncSession, device_id: str, fallback: int | None = None
) -> int | None:
    device = await get_by_device_id(db, device_id)
    if device and device.elder_id:
        return device.elder_id
    return fallback


async def self_check(db: AsyncSession, ping_timeout: float = 3.0) -> dict:
    """主动自检：复核每台设备是否真的在线，而不是只看数据库里的状态。

    手表是被动广播的，只能按最近一次数据判断；机器狗可以下发 ping 等 pong，
    确认它不只是"最近上报过"，而是现在还能收指令。
    """
    started = time.perf_counter()
    now = datetime.now()
    offline_after = timedelta(seconds=settings.device_offline_seconds)

    devices = list((await db.execute(select(Device).order_by(Device.id))).scalars().all())
    elders = {
        elder.id: elder.name
        for elder in (await db.execute(select(Elder))).scalars().unique().all()
    }

    # 每台设备最近一条健康数据，用来判断真机数据通道是否还在出数
    latest_samples: dict[str, HealthRecord] = {}
    for device in devices:
        if device.device_type != DeviceType.WATCH:
            continue
        row = await db.execute(
            select(HealthRecord)
            .where(HealthRecord.device_id == device.device_id)
            .order_by(HealthRecord.recorded_at.desc())
            .limit(1)
        )
        record = row.scalars().first()
        if record:
            latest_samples[device.device_id] = record

    # 机器狗并发 ping，避免逐台串行等待超时
    dogs = [d for d in devices if d.device_type == DeviceType.DOG]
    pong_results: dict[str, dict | None] = {}
    if dogs and mqtt_bridge.connected:
        async def probe_one(device: Device) -> tuple[str, dict | None]:
            nonce = probe.new_nonce()
            waiter = asyncio.create_task(probe.wait_for_pong(nonce, ping_timeout))
            await commands.ping(device.device_id, nonce, device.elder_id)
            return device.device_id, await waiter

        for device_id, result in await asyncio.gather(*(probe_one(d) for d in dogs)):
            pong_results[device_id] = result

    reports = []
    changed = 0
    for device in devices:
        seen_seconds = (
            (now - device.last_seen_at).total_seconds() if device.last_seen_at else None
        )
        heartbeat_alive = seen_seconds is not None and seen_seconds <= offline_after.total_seconds()

        sample = latest_samples.get(device.device_id)
        sample_seconds = (
            (now - sample.recorded_at).total_seconds() if sample else None
        )

        issues: list[str] = []
        advice = ""
        probe_kind = "passive"
        responded: bool | None = None

        if device.device_type == DeviceType.DOG:
            if not mqtt_bridge.connected:
                probe_kind = "skipped"
                issues.append("MQTT 未连接，无法下发探活指令")
                advice = "先确认 MQTT Broker 是否启动"
            else:
                probe_kind = "ping"
                responded = pong_results.get(device.device_id) is not None
                if not responded:
                    issues.append(f"{ping_timeout:.0f} 秒内没有响应探活指令")
                    advice = "确认机器狗进程是否在运行、是否连着同一个 Broker"

        online = responded if responded is not None else heartbeat_alive

        if not online and probe_kind != "ping":
            if seen_seconds is None:
                issues.append("从未收到过该设备的数据")
                advice = advice or "确认设备已开机，并检查设备编号是否填写正确"
            else:
                issues.append(f"已有 {int(seen_seconds)} 秒没有心跳")
                advice = advice or "检查设备电量与网络，手环还需确认采集网关是否在运行"

        if device.elder_id is None:
            issues.append("尚未绑定老人，上报的数据不会入库")
            advice = advice or "在本页把它绑定到对应老人"

        if online and device.battery is not None and device.battery < 20:
            issues.append(f"电量仅剩 {device.battery}%")
            advice = advice or "尽快充电，低电量会拦截外出申请"

        if (
            device.device_type == DeviceType.WATCH
            and online
            and sample_seconds is not None
            and sample_seconds > settings.device_offline_seconds
        ):
            issues.append(f"设备在线但已有 {int(sample_seconds)} 秒没有新的体征数据")
            advice = advice or "手环可能没戴上或已休眠，按亮屏幕试试"

        new_status = DeviceStatus.ONLINE if online else DeviceStatus.OFFLINE
        previous = device.status
        if previous != new_status:
            device.status = new_status
            changed += 1

        reports.append(
            {
                "device_id": device.device_id,
                "device_type": device.device_type,
                "name": device.name,
                "elder_id": device.elder_id,
                "elder_name": elders.get(device.elder_id, "") if device.elder_id else "",
                "status": new_status,
                "previous_status": previous,
                "status_changed": previous != new_status,
                "battery": device.battery,
                "last_seen_at": device.last_seen_at,
                "seconds_since_seen": round(seen_seconds, 1) if seen_seconds is not None else None,
                "last_sample_at": sample.recorded_at if sample else None,
                "seconds_since_sample": (
                    round(sample_seconds, 1) if sample_seconds is not None else None
                ),
                "data_source": sample.source if sample else "",
                "probe": probe_kind,
                "responded": responded,
                "issues": issues,
                "advice": advice,
            }
        )

    await db.flush()

    online_count = sum(1 for item in reports if item["status"] == DeviceStatus.ONLINE)
    result = {
        "checked_at": now,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "mqtt": mqtt_bridge.status(),
        "gateways": gateway_registry.snapshot(),
        "summary": {
            "total": len(reports),
            "online": online_count,
            "offline": len(reports) - online_count,
            "unbound": sum(1 for item in reports if item["elder_id"] is None),
            "with_issues": sum(1 for item in reports if item["issues"]),
            "status_changed": changed,
        },
        "devices": reports,
    }
    logger.info(
        "设备自检完成：%d 台在线 / 共 %d 台，状态修正 %d 台，耗时 %dms",
        online_count,
        len(reports),
        changed,
        result["duration_ms"],
    )
    return result


async def device_status_snapshot(db: AsyncSession, elder_id: int) -> dict:
    """外出评估需要的设备可用性快照。"""
    devices = await list_devices(db, elder_id=elder_id)
    snapshot: dict = {"watch": None, "dog": None}
    for device in devices:
        item = {
            "device_id": device.device_id,
            "name": device.name,
            "status": device.status,
            "battery": device.battery,
            "last_seen_at": device.last_seen_at,
            "online": device.status == DeviceStatus.ONLINE,
        }
        if device.device_type == DeviceType.WATCH and snapshot["watch"] is None:
            snapshot["watch"] = item
        elif device.device_type == DeviceType.DOG and snapshot["dog"] is None:
            snapshot["dog"] = item
    return snapshot
