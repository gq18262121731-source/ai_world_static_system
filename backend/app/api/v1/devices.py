from fastapi import APIRouter, HTTPException, Request

from app.core.deps import AdminUser, CurrentUser, DbSession, get_accessible_elder
from app.iot import commands
from app.iot.gateways import gateway_registry
from app.iot.mqtt_client import mqtt_bridge
from app.models import Device
from app.schemas.common import Msg
from app.schemas.device import DeviceCommand, DeviceCreate, DeviceOut, DeviceUpdate
from app.services import audit_service, device_service

router = APIRouter(prefix="/devices", tags=["设备管理"])


@router.get("", response_model=list[DeviceOut], summary="设备列表")
async def list_devices(
    db: DbSession,
    _: CurrentUser,
    elder_id: int | None = None,
    device_type: str | None = None,
):
    devices = await device_service.list_devices(db, elder_id, device_type)
    return [DeviceOut.model_validate(d) for d in devices]


@router.post("", response_model=DeviceOut, summary="注册设备")
async def create_device(
    payload: DeviceCreate, db: DbSession, operator: AdminUser, request: Request
):
    data = payload.model_dump()
    # 填成 54:10:26:01:00:DC 这种带分隔符的写法时自动规范化，
    # 否则会和网关上报用的编号对不上，数据永远进不来
    data["device_id"] = device_service.normalize_device_id(data["device_id"])
    existing = await device_service.get_by_device_id(db, data["device_id"])
    if existing:
        raise HTTPException(status_code=400, detail="设备编号已存在")
    device = Device(**data)
    device.name = device.name or device.device_id
    db.add(device)
    await audit_service.record(
        db, operator, "create_device", "device", payload.device_id, request=request
    )
    await db.commit()
    await db.refresh(device)
    return DeviceOut.model_validate(device)


@router.put("/{device_id}", response_model=DeviceOut, summary="更新设备(绑定老人)")
async def update_device(
    device_id: str,
    payload: DeviceUpdate,
    db: DbSession,
    operator: AdminUser,
    request: Request,
):
    device = await device_service.get_by_device_id(db, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    # 用 exclude_unset 而不是 exclude_none：显式传 elder_id=null 表示解绑，
    # 未传的字段才保持不变
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(device, key, value)
    await audit_service.record(
        db, operator, "update_device", "device", device_id, changes, request
    )
    await db.commit()
    await db.refresh(device)
    return DeviceOut.model_validate(device)


@router.delete("/{device_id}", response_model=Msg, summary="删除设备")
async def delete_device(device_id: str, db: DbSession, _: AdminUser):
    device = await device_service.get_by_device_id(db, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    await db.delete(device)
    await db.commit()
    return Msg(message="已删除")


@router.post("/{device_id}/command", response_model=Msg, summary="向机器狗下发指令")
async def send_command(
    device_id: str,
    payload: DeviceCommand,
    db: DbSession,
    operator: AdminUser,
    request: Request,
):
    device = await device_service.get_by_device_id(db, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    result = await commands.send_command(
        device_id, payload.command, payload.payload, elder_id=device.elder_id
    )
    await audit_service.record(
        db, operator, "device_command", "device", device_id, payload.model_dump(), request
    )
    await db.commit()
    return Msg(message="指令已下发", data=result)


@router.post("/self-check", summary="设备自检：主动探活并复核在线状态")
async def self_check(
    db: DbSession,
    operator: CurrentUser,
    request: Request,
    ping_timeout: float = 3.0,
):
    """不只是重新读数据库，而是给机器狗下发探活指令、复核手环数据通道。"""
    result = await device_service.self_check(db, ping_timeout=ping_timeout)
    await audit_service.record(
        db,
        operator,
        "device_self_check",
        "device",
        "",
        {"summary": result["summary"]},
        request,
    )
    await db.commit()
    return result


@router.get("/gateways", summary="采集网关状态")
async def gateways(_: CurrentUser):
    return gateway_registry.snapshot()


@router.get("/status/snapshot", summary="某位老人的设备状态快照")
async def status_snapshot(elder_id: int, db: DbSession, user: CurrentUser):
    await get_accessible_elder(db, user, elder_id)
    return await device_service.device_status_snapshot(db, elder_id)


@router.get("/mqtt/status", summary="MQTT 连接状态")
async def mqtt_status(_: CurrentUser):
    return mqtt_bridge.status()
