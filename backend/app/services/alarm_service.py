from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache
from app.core.events import hub
from app.core.logging import get_logger
from app.iot import commands
from app.models import Alarm, AlarmNotification, Elder, FallEvent, User
from app.models.enums import AlarmLevel, AlarmStatus, AlarmType
from app.services import device_service, prompt_service

logger = get_logger(__name__)

# 手表与机器狗在该时间窗内上报的跌倒视为同一次事件
FALL_FUSION_WINDOW_SECONDS = 30
# 机器狗语音确认的等待时长
FALL_CONFIRM_TIMEOUT_SECONDS = 30
# 告警升级的时间间隔
ESCALATION_INTERVAL_SECONDS = 60


async def create_alarm(
    db: AsyncSession,
    elder_id: int,
    alarm_type: str,
    level: str,
    title: str,
    description: str = "",
    status: str = AlarmStatus.PENDING,
    lat: float | None = None,
    lng: float | None = None,
    task_id: int | None = None,
    data: dict | None = None,
    notify: bool = True,
) -> Alarm:
    alarm = Alarm(
        elder_id=elder_id,
        alarm_type=alarm_type,
        level=level,
        status=status,
        title=title,
        description=description,
        triggered_at=datetime.now(),
        lat=lat,
        lng=lng,
        task_id=task_id,
        data=data or {},
    )
    db.add(alarm)
    await db.flush()
    logger.warning("告警 #%s [%s/%s] %s", alarm.id, alarm_type, level, title)

    await hub.broadcast(
        "alarm",
        {
            "id": alarm.id,
            "elder_id": elder_id,
            "alarm_type": alarm_type,
            "level": level,
            "status": alarm.status,
            "title": title,
            "description": description,
            "triggered_at": alarm.triggered_at,
            "task_id": task_id,
        },
        elder_id=elder_id,
    )
    if notify:
        await escalate(db, alarm)
    return alarm


async def _notify(
    db: AsyncSession,
    alarm: Alarm,
    channel: str,
    target: str,
    target_name: str,
    content: str,
) -> AlarmNotification:
    """通知落库并推送。真实环境在此接入短信/语音外呼/App 推送网关。"""
    notification = AlarmNotification(
        alarm_id=alarm.id,
        channel=channel,
        target=target,
        target_name=target_name,
        content=content,
        status="sent",
        sent_at=datetime.now(),
    )
    db.add(notification)
    await db.flush()
    await hub.broadcast(
        "alarm_notification",
        {
            "alarm_id": alarm.id,
            "channel": channel,
            "target": target,
            "target_name": target_name,
            "content": content,
            "sent_at": notification.sent_at,
        },
        elder_id=alarm.elder_id,
    )
    logger.info("告警 #%s 通知 %s(%s) via %s", alarm.id, target_name, target, channel)
    return notification


async def escalate(db: AsyncSession, alarm: Alarm) -> None:
    """告警分级升级: 家属 -> 紧急联系人 -> 社区服务人员。"""
    elder = await db.get(Elder, alarm.elder_id)
    elder_name = elder.name if elder else f"老人#{alarm.elder_id}"
    alarm.escalation_level += 1
    level = alarm.escalation_level
    content = f"【{alarm.title}】{elder_name}：{alarm.description}"

    if level == 1:
        family_users = await _family_users(db, alarm.elder_id)
        for user in family_users:
            await _notify(db, alarm, "app_push", user.phone or user.username, user.real_name or user.username, content)
        if not family_users:
            await _notify(db, alarm, "websocket", "family", "家属端", content)
    elif level == 2:
        contacts = sorted(
            elder.emergency_contacts if elder else [], key=lambda c: c.priority
        )
        if contacts:
            for contact in contacts:
                await _notify(db, alarm, "sms", contact.phone, contact.name, content)
        else:
            await _notify(db, alarm, "sms", "", "紧急联系人(未配置)", content)
    else:
        staff = await db.execute(select(User).where(User.role.in_(["admin", "staff"])))
        for user in staff.scalars().all():
            await _notify(
                db, alarm, "voice_call", user.phone or user.username,
                user.real_name or user.username, content,
            )
    await db.flush()


async def _family_users(db: AsyncSession, elder_id: int) -> list[User]:
    from app.models import FamilyLink

    result = await db.execute(
        select(User)
        .join(FamilyLink, FamilyLink.user_id == User.id)
        .where(FamilyLink.elder_id == elder_id, User.is_active.is_(True))
    )
    return list(result.scalars().unique().all())


async def handle_fall_event(
    db: AsyncSession,
    elder_id: int,
    source: str,
    device_id: str = "",
    confidence: float = 0.9,
    lat: float | None = None,
    lng: float | None = None,
    detected_at: datetime | None = None,
    raw: dict | None = None,
) -> tuple[FallEvent, Alarm, bool]:
    """跌倒事件入口: 双源融合去重 -> 建告警 -> 机器狗语音确认。

    返回 (事件, 告警, 是否为新告警)。
    """
    detected_at = detected_at or datetime.now()
    window_start = detected_at - timedelta(seconds=FALL_FUSION_WINDOW_SECONDS)

    recent = await db.execute(
        select(FallEvent)
        .where(
            FallEvent.elder_id == elder_id,
            FallEvent.detected_at >= window_start,
            FallEvent.alarm_id.is_not(None),
        )
        .order_by(FallEvent.id.desc())
        .limit(1)
    )
    previous = recent.scalars().first()

    event = FallEvent(
        elder_id=elder_id,
        source=source,
        device_id=device_id,
        detected_at=detected_at,
        confidence=confidence,
        lat=lat,
        lng=lng,
        raw=raw or {},
    )

    if previous is not None:
        alarm = await db.get(Alarm, previous.alarm_id)
        event.alarm_id = previous.alarm_id
        event.merged = True
        db.add(event)
        if alarm:
            data = dict(alarm.data or {})
            sources = set(data.get("sources", []))
            sources.add(source)
            data["sources"] = sorted(sources)
            # 双源同时确认, 置信度提升
            data["fused_confidence"] = round(
                min(0.99, max(data.get("fused_confidence", confidence), confidence) + 0.1),
                2,
            )
            if len(sources) > 1:
                data["fusion"] = "手表与机器狗同时检测到跌倒"
                alarm.description += " 手表与机器狗双源确认，可信度高。"
            alarm.data = data
            await db.flush()
        await db.flush()
        logger.info("跌倒事件与告警 #%s 融合(来源 %s)", previous.alarm_id, source)
        return event, alarm, False  # type: ignore[return-value]

    source_label = {"watch": "手表", "dog": "机器狗"}.get(source, source)
    alarm = await create_alarm(
        db,
        elder_id=elder_id,
        alarm_type=AlarmType.FALL,
        level=AlarmLevel.CRITICAL,
        title="检测到疑似跌倒",
        description=f"{source_label}检测到跌倒动作(置信度 {confidence:.0%})，正在语音确认老人状态。",
        status=AlarmStatus.CONFIRMING,
        lat=lat,
        lng=lng,
        data={
            "sources": [source],
            "fused_confidence": confidence,
            "confirm_deadline": (
                detected_at + timedelta(seconds=FALL_CONFIRM_TIMEOUT_SECONDS)
            ).isoformat(),
        },
        notify=False,
    )
    event.alarm_id = alarm.id
    db.add(event)
    await db.flush()

    dog = await device_service.get_elder_device(db, elder_id, "dog")
    if dog:
        await commands.ask_confirm(
            dog.device_id,
            elder_id,
            await prompt_service.get_template(db, "fall_confirm_question"),
            alarm.id,
            FALL_CONFIRM_TIMEOUT_SECONDS,
        )
    await cache.set_json(
        f"fall_confirm:{elder_id}",
        {"alarm_id": alarm.id, "asked_at": detected_at.isoformat()},
        ex=FALL_CONFIRM_TIMEOUT_SECONDS * 4,
    )
    return event, alarm, True


async def pending_fall_alarm(db: AsyncSession, elder_id: int) -> Alarm | None:
    cached = await cache.get_json(f"fall_confirm:{elder_id}")
    if cached:
        alarm = await db.get(Alarm, cached["alarm_id"])
        if alarm and alarm.status == AlarmStatus.CONFIRMING:
            return alarm
    result = await db.execute(
        select(Alarm)
        .where(
            Alarm.elder_id == elder_id,
            Alarm.alarm_type == AlarmType.FALL,
            Alarm.status == AlarmStatus.CONFIRMING,
        )
        .order_by(Alarm.id.desc())
        .limit(1)
    )
    return result.scalars().first()


async def resolve_fall_confirmation(
    db: AsyncSession, alarm: Alarm, elder_ok: bool, reply_text: str
) -> Alarm:
    """处理老人对"您还好吗"的语音回应。"""
    data = dict(alarm.data or {})
    data["elder_reply"] = reply_text
    alarm.data = data
    if elder_ok:
        alarm.status = AlarmStatus.RESOLVED
        alarm.resolved_at = datetime.now()
        alarm.handled_by = "语音确认"
        alarm.handle_note = f"老人回应“{reply_text}”，确认无碍。"
        alarm.level = AlarmLevel.INFO
        alarm.description += " 老人语音确认无碍，告警自动关闭。"
        await db.flush()
        await _notify(db, alarm, "websocket", "family", "家属端", f"{alarm.title}：老人已确认无碍")
    else:
        alarm.status = AlarmStatus.PENDING
        alarm.description += f" 老人回应“{reply_text}”，需要立即救助。"
        await db.flush()
        await escalate(db, alarm)
        await escalate(db, alarm)
    await cache.delete(f"fall_confirm:{alarm.elder_id}")
    await hub.broadcast(
        "alarm_updated",
        {
            "id": alarm.id,
            "elder_id": alarm.elder_id,
            "status": alarm.status,
            "level": alarm.level,
            "handle_note": alarm.handle_note,
        },
        elder_id=alarm.elder_id,
    )
    return alarm


async def check_pending_confirmations(db: AsyncSession) -> None:
    """确认超时未应答的跌倒告警自动升级。"""
    deadline = datetime.now() - timedelta(seconds=FALL_CONFIRM_TIMEOUT_SECONDS)
    result = await db.execute(
        select(Alarm).where(
            Alarm.alarm_type == AlarmType.FALL,
            Alarm.status == AlarmStatus.CONFIRMING,
            Alarm.triggered_at <= deadline,
        )
    )
    for alarm in result.scalars().all():
        alarm.status = AlarmStatus.PENDING
        alarm.description += " 语音确认无应答，已自动升级通知家属与紧急联系人。"
        await db.flush()
        await escalate(db, alarm)
        await escalate(db, alarm)
        await cache.delete(f"fall_confirm:{alarm.elder_id}")
        dog = await device_service.get_elder_device(db, alarm.elder_id, "dog")
        if dog:
            await commands.alarm_broadcast(
                dog.device_id,
                alarm.elder_id,
                await prompt_service.get_template(db, "fall_no_response_broadcast"),
                AlarmLevel.CRITICAL,
            )
        await hub.broadcast(
            "alarm_updated",
            {
                "id": alarm.id,
                "elder_id": alarm.elder_id,
                "status": alarm.status,
                "escalation_level": alarm.escalation_level,
            },
            elder_id=alarm.elder_id,
        )


async def escalate_unhandled(db: AsyncSession) -> None:
    """长时间无人受理的严重告警继续升级。"""
    deadline = datetime.now() - timedelta(seconds=ESCALATION_INTERVAL_SECONDS * 3)
    result = await db.execute(
        select(Alarm).where(
            Alarm.status == AlarmStatus.PENDING,
            Alarm.level == AlarmLevel.CRITICAL,
            Alarm.triggered_at <= deadline,
            Alarm.escalation_level < 3,
        )
    )
    for alarm in result.scalars().all():
        await escalate(db, alarm)


async def trigger_sos(
    db: AsyncSession, elder_id: int, note: str = "", lat: float | None = None,
    lng: float | None = None,
) -> Alarm:
    alarm = await create_alarm(
        db,
        elder_id=elder_id,
        alarm_type=AlarmType.SOS,
        level=AlarmLevel.CRITICAL,
        title="老人主动求助",
        description=note or "老人通过机器狗发出求助请求。",
        lat=lat,
        lng=lng,
    )
    await escalate(db, alarm)
    dog = await device_service.get_elder_device(db, elder_id, "dog")
    if dog:
        await commands.alarm_broadcast(
            dog.device_id,
            elder_id,
            await prompt_service.get_template(db, "sos_broadcast"),
            AlarmLevel.CRITICAL,
        )
    return alarm


async def check_health_anomaly(
    db: AsyncSession, elder_id: int, abnormal_items: list[str], risk_score: float
) -> Alarm | None:
    """健康指标出现危险值时告警, 同一天同类只告警一次。"""
    if risk_score < 30 or not abnormal_items:
        return None
    key = f"health_alarm:{elder_id}:{datetime.now().strftime('%Y%m%d%H')}"
    if await cache.get_json(key):
        return None
    await cache.set_json(key, {"fired": True}, ex=3600)
    return await create_alarm(
        db,
        elder_id=elder_id,
        alarm_type=AlarmType.HEALTH,
        level=AlarmLevel.WARNING if risk_score < 60 else AlarmLevel.CRITICAL,
        title="健康指标异常",
        description="；".join(abnormal_items),
        data={"risk_score": risk_score, "items": abnormal_items},
    )


async def list_alarms(
    db: AsyncSession,
    elder_id: int | None = None,
    status: str | None = None,
    level: str | None = None,
    alarm_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Alarm], int]:
    stmt = select(Alarm)
    count_stmt = select(func.count(Alarm.id))
    conditions = []
    if elder_id is not None:
        conditions.append(Alarm.elder_id == elder_id)
    if status:
        conditions.append(Alarm.status == status)
    if level:
        conditions.append(Alarm.level == level)
    if alarm_type:
        conditions.append(Alarm.alarm_type == alarm_type)
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    total = int((await db.execute(count_stmt)).scalar() or 0)
    stmt = stmt.order_by(Alarm.id.desc()).limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars().all()), total


async def get_notifications(db: AsyncSession, alarm_id: int) -> list[AlarmNotification]:
    result = await db.execute(
        select(AlarmNotification)
        .where(AlarmNotification.alarm_id == alarm_id)
        .order_by(AlarmNotification.id)
    )
    return list(result.scalars().all())


async def handle_alarm(
    db: AsyncSession, alarm: Alarm, status: str, note: str, operator: str
) -> Alarm:
    alarm.status = status
    alarm.handle_note = note
    alarm.handled_by = operator
    now = datetime.now()
    if status == AlarmStatus.ACKED:
        alarm.acknowledged_at = now
    elif status in (AlarmStatus.RESOLVED, AlarmStatus.FALSE_ALARM):
        alarm.acknowledged_at = alarm.acknowledged_at or now
        alarm.resolved_at = now
    await db.flush()
    await hub.broadcast(
        "alarm_updated",
        {
            "id": alarm.id,
            "elder_id": alarm.elder_id,
            "status": alarm.status,
            "handled_by": operator,
            "handle_note": note,
        },
        elder_id=alarm.elder_id,
    )
    return alarm


async def get_alarm(db: AsyncSession, alarm_id: int) -> Alarm | None:
    return await db.get(Alarm, alarm_id)
