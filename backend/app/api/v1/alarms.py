from fastapi import APIRouter, HTTPException, Request

from app.core.deps import CurrentUser, DbSession, accessible_elder_ids, get_accessible_elder
from app.models import Elder
from app.models.enums import AlarmStatus
from app.schemas.alarm import (
    AlarmDetailOut,
    AlarmHandleRequest,
    AlarmNotificationOut,
    AlarmOut,
    FallEventIn,
)
from app.services import alarm_service, audit_service

router = APIRouter(prefix="/alarms", tags=["告警中心"])


@router.get("", summary="告警列表")
async def list_alarms(
    db: DbSession,
    user: CurrentUser,
    elder_id: int | None = None,
    status: str | None = None,
    level: str | None = None,
    alarm_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
):
    if elder_id is not None:
        await get_accessible_elder(db, user, elder_id)
    else:
        allowed = await accessible_elder_ids(db, user)
        if allowed is not None and len(allowed) == 1:
            elder_id = allowed[0]
    items, total = await alarm_service.list_alarms(
        db,
        elder_id,
        status,
        level,
        alarm_type,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    return {
        "items": [AlarmOut.model_validate(a).model_dump(mode="json") for a in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{alarm_id}", response_model=AlarmDetailOut, summary="告警详情")
async def get_alarm(alarm_id: int, db: DbSession, user: CurrentUser):
    alarm = await alarm_service.get_alarm(db, alarm_id)
    if not alarm:
        raise HTTPException(status_code=404, detail="告警不存在")
    await get_accessible_elder(db, user, alarm.elder_id)
    elder = await db.get(Elder, alarm.elder_id)
    notifications = await alarm_service.get_notifications(db, alarm_id)
    detail = AlarmDetailOut.model_validate(alarm)
    detail.elder_name = elder.name if elder else ""
    detail.notifications = [
        AlarmNotificationOut.model_validate(n) for n in notifications
    ]
    return detail


@router.post("/{alarm_id}/handle", response_model=AlarmOut, summary="处置告警")
async def handle_alarm(
    alarm_id: int,
    payload: AlarmHandleRequest,
    db: DbSession,
    user: CurrentUser,
    request: Request,
):
    alarm = await alarm_service.get_alarm(db, alarm_id)
    if not alarm:
        raise HTTPException(status_code=404, detail="告警不存在")
    await get_accessible_elder(db, user, alarm.elder_id)
    if payload.status not in (
        AlarmStatus.ACKED,
        AlarmStatus.RESOLVED,
        AlarmStatus.FALSE_ALARM,
    ):
        raise HTTPException(status_code=400, detail="不支持的处置状态")
    await alarm_service.handle_alarm(
        db, alarm, payload.status, payload.note, user.real_name or user.username
    )
    await audit_service.record(
        db, user, "handle_alarm", "alarm", alarm_id, payload.model_dump(), request
    )
    await db.commit()
    await db.refresh(alarm)
    return AlarmOut.model_validate(alarm)


@router.post("/fall", response_model=AlarmOut, summary="上报跌倒事件(设备/模拟器)")
async def report_fall(elder_id: int, payload: FallEventIn, db: DbSession, user: CurrentUser):
    await get_accessible_elder(db, user, elder_id)
    _event, alarm, _is_new = await alarm_service.handle_fall_event(
        db,
        elder_id=elder_id,
        source=payload.source,
        device_id=payload.device_id,
        confidence=payload.confidence,
        lat=payload.lat,
        lng=payload.lng,
        detected_at=payload.detected_at,
        raw=payload.raw,
    )
    await db.commit()
    await db.refresh(alarm)
    return AlarmOut.model_validate(alarm)


@router.post("/sos", response_model=AlarmOut, summary="触发 SOS 求助")
async def trigger_sos(elder_id: int, db: DbSession, user: CurrentUser, note: str = ""):
    await get_accessible_elder(db, user, elder_id)
    alarm = await alarm_service.trigger_sos(db, elder_id, note)
    await db.commit()
    await db.refresh(alarm)
    return AlarmOut.model_validate(alarm)
