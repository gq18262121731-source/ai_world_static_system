from datetime import date, datetime, time, timedelta

from fastapi import APIRouter
from sqlalchemy import func, select

from app.ai.factory import ai_status
from app.core.deps import CurrentUser, DbSession, accessible_elder_ids
from app.iot.mqtt_client import mqtt_bridge
from app.models import (
    Alarm,
    Device,
    Elder,
    OutingDecision,
    OutingRequest,
    OutingTask,
)
from app.models.enums import AlarmStatus, DeviceStatus, OutingTaskStatus
from app.schemas.alarm import AlarmOut
from app.services import audit_service

router = APIRouter(prefix="/dashboard", tags=["总览看板"])


@router.get("/overview", summary="平台总览")
async def overview(db: DbSession, user: CurrentUser):
    allowed = await accessible_elder_ids(db, user)
    today_start = datetime.combine(date.today(), time.min)

    def _scope(stmt, column):
        return stmt if allowed is None else stmt.where(column.in_(allowed or [-1]))

    elder_total = (
        await db.execute(_scope(select(func.count(Elder.id)), Elder.id))
    ).scalar() or 0
    device_total = (
        await db.execute(_scope(select(func.count(Device.id)), Device.elder_id))
    ).scalar() or 0
    device_online = (
        await db.execute(
            _scope(
                select(func.count(Device.id)).where(
                    Device.status == DeviceStatus.ONLINE
                ),
                Device.elder_id,
            )
        )
    ).scalar() or 0
    outing_today = (
        await db.execute(
            _scope(
                select(func.count(OutingTask.id)).where(
                    OutingTask.started_at >= today_start
                ),
                OutingTask.elder_id,
            )
        )
    ).scalar() or 0
    outing_active = (
        await db.execute(
            _scope(
                select(func.count(OutingTask.id)).where(
                    OutingTask.status == OutingTaskStatus.ACTIVE
                ),
                OutingTask.elder_id,
            )
        )
    ).scalar() or 0
    alarm_pending = (
        await db.execute(
            _scope(
                select(func.count(Alarm.id)).where(
                    Alarm.status.in_([AlarmStatus.PENDING, AlarmStatus.CONFIRMING])
                ),
                Alarm.elder_id,
            )
        )
    ).scalar() or 0
    alarm_today = (
        await db.execute(
            _scope(
                select(func.count(Alarm.id)).where(Alarm.triggered_at >= today_start),
                Alarm.elder_id,
            )
        )
    ).scalar() or 0

    decision_rows = await db.execute(
        _scope(
            select(OutingDecision.decision, func.count(OutingDecision.id)).group_by(
                OutingDecision.decision
            ),
            OutingDecision.elder_id,
        )
    )
    decision_distribution = {row[0]: row[1] for row in decision_rows.all()}

    recent_alarms = await db.execute(
        _scope(select(Alarm).order_by(Alarm.id.desc()).limit(8), Alarm.elder_id)
    )

    return {
        "elder_total": elder_total,
        "device_total": device_total,
        "device_online": device_online,
        "outing_today": outing_today,
        "outing_active": outing_active,
        "alarm_pending": alarm_pending,
        "alarm_today": alarm_today,
        "decision_distribution": decision_distribution,
        "recent_alarms": [
            AlarmOut.model_validate(a).model_dump(mode="json")
            for a in recent_alarms.scalars().all()
        ],
        "mqtt": mqtt_bridge.status(),
        "ai": ai_status(),
    }


@router.get("/outing-trend", summary="近 N 天外出决策趋势")
async def outing_trend(db: DbSession, user: CurrentUser, days: int = 7):
    allowed = await accessible_elder_ids(db, user)
    since = datetime.combine(date.today() - timedelta(days=days - 1), time.min)
    stmt = (
        select(OutingRequest.requested_at, OutingDecision.decision)
        .join(OutingDecision, OutingDecision.request_id == OutingRequest.id)
        .where(OutingRequest.requested_at >= since)
    )
    if allowed is not None:
        stmt = stmt.where(OutingRequest.elder_id.in_(allowed or [-1]))
    rows = (await db.execute(stmt)).all()

    buckets: dict[str, dict[str, int]] = {}
    for index in range(days):
        day = (date.today() - timedelta(days=days - 1 - index)).isoformat()
        buckets[day] = {"allow": 0, "conditional": 0, "deny": 0}
    for requested_at, decision in rows:
        key = requested_at.date().isoformat()
        if key in buckets and decision in buckets[key]:
            buckets[key][decision] += 1
    return [{"date": day, **counts} for day, counts in buckets.items()]


@router.get("/audit-logs", summary="操作审计日志")
async def audit_logs(db: DbSession, _: CurrentUser, limit: int = 100):
    logs = await audit_service.list_logs(db, limit=limit)
    return [
        {
            "id": log.id,
            "username": log.username,
            "action": log.action,
            "target_type": log.target_type,
            "target_id": log.target_id,
            "detail": log.detail,
            "ip": log.ip,
            "created_at": log.created_at,
        }
        for log in logs
    ]
