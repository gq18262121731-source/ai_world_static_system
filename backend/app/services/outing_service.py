from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import hub
from app.core.geo import haversine_m
from app.core.logging import get_logger
from app.iot import commands
from app.models import (
    Elder,
    OutingDecision,
    OutingRequest,
    OutingTask,
    TrackPoint,
)
from app.models.enums import (
    AlarmLevel,
    AlarmType,
    OutingDecisionType,
    OutingTaskStatus,
)
from app.schemas.outing import OutingEvaluation, OutingStats, RuleReport
from app.services import alarm_service, device_service, prompt_service

logger = get_logger(__name__)

# 到达计划时长的这个比例时先做一次语音提醒
REMIND_RATIO = 0.8
# 越界提醒的缓冲距离(米)
GEOFENCE_BUFFER_M = 50


async def get_active_task(db: AsyncSession, elder_id: int) -> OutingTask | None:
    result = await db.execute(
        select(OutingTask)
        .where(
            OutingTask.elder_id == elder_id,
            OutingTask.status == OutingTaskStatus.ACTIVE,
        )
        .order_by(OutingTask.id.desc())
    )
    return result.scalars().first()


async def get_stats(db: AsyncSession, elder_id: int) -> OutingStats:
    now = datetime.now()
    today_start = datetime.combine(date.today(), time.min)
    week_start = today_start - timedelta(days=now.weekday())
    month_start = today_start.replace(day=1)

    async def _count(since: datetime) -> int:
        result = await db.execute(
            select(func.count(OutingTask.id)).where(
                OutingTask.elder_id == elder_id, OutingTask.started_at >= since
            )
        )
        return int(result.scalar() or 0)

    stats = OutingStats(
        today_count=await _count(today_start),
        week_count=await _count(week_start),
        month_count=await _count(month_start),
    )

    last = await db.execute(
        select(OutingTask)
        .where(OutingTask.elder_id == elder_id)
        .order_by(OutingTask.started_at.desc())
        .limit(1)
    )
    last_task = last.scalars().first()
    if last_task:
        stats.last_outing_at = last_task.started_at
        if last_task.ended_at:
            stats.minutes_since_last_return = int(
                (now - last_task.ended_at).total_seconds() // 60
            )

    today_tasks = await db.execute(
        select(OutingTask).where(
            OutingTask.elder_id == elder_id, OutingTask.started_at >= today_start
        )
    )
    total_minutes = 0
    for task in today_tasks.scalars().all():
        end = task.ended_at or now
        total_minutes += int((end - task.started_at).total_seconds() // 60)
    stats.today_total_minutes = total_minutes

    finished = await db.execute(
        select(OutingTask)
        .where(
            OutingTask.elder_id == elder_id,
            OutingTask.status == OutingTaskStatus.COMPLETED,
            OutingTask.ended_at.is_not(None),
        )
        .order_by(OutingTask.id.desc())
        .limit(20)
    )
    durations = [
        (t.ended_at - t.started_at).total_seconds() / 60
        for t in finished.scalars().all()
    ]
    if durations:
        stats.avg_duration_minutes = round(sum(durations) / len(durations), 1)

    active = await get_active_task(db, elder_id)
    stats.active_task_id = active.id if active else None
    return stats


async def create_request(
    db: AsyncSession,
    elder_id: int,
    text: str,
    channel: str = "voice",
    device_id: str = "",
) -> OutingRequest:
    request = OutingRequest(
        elder_id=elder_id,
        device_id=device_id,
        channel=channel,
        raw_text=text,
        requested_at=datetime.now(),
    )
    db.add(request)
    await db.flush()
    return request


async def save_decision(
    db: AsyncSession,
    request: OutingRequest,
    evaluation: OutingEvaluation,
    rule_report: RuleReport,
    health_snapshot: dict,
    weather_snapshot: dict,
    outing_stats: dict,
) -> OutingDecision:
    decision = OutingDecision(
        request_id=request.id,
        elder_id=request.elder_id,
        decision=evaluation.decision,
        reasons=evaluation.reasons,
        advice=evaluation.advice,
        limits=evaluation.limits.model_dump(),
        rule_report=rule_report.model_dump(mode="json"),
        health_snapshot=health_snapshot,
        weather_snapshot=weather_snapshot,
        outing_stats=outing_stats,
        llm_raw=evaluation.llm_raw,
        llm_provider=evaluation.llm_provider,
        overridden_by_rules=evaluation.overridden_by_rules,
        confidence=evaluation.confidence,
        speech_text=evaluation.speech_text,
        extras=evaluation.extras(),
    )
    db.add(decision)
    await db.flush()
    await hub.broadcast(
        "outing_decision",
        {
            "decision_id": decision.id,
            "request_id": request.id,
            "elder_id": request.elder_id,
            "decision": decision.decision,
            "reasons": decision.reasons,
            "advice": decision.advice,
            "limits": decision.limits,
            "speech_text": decision.speech_text,
        },
        elder_id=request.elder_id,
    )
    return decision


async def start_task(
    db: AsyncSession, elder: Elder, decision: OutingDecision
) -> OutingTask | None:
    """外出评估通过后创建任务并让机器狗进入跟随模式。"""
    if decision.decision == OutingDecisionType.DENY:
        return None
    existing = await get_active_task(db, elder.id)
    if existing:
        return existing

    dog = await device_service.get_elder_device(db, elder.id, "dog")
    watch = await device_service.get_elder_device(db, elder.id, "watch")
    limits = decision.limits or {}
    duration = int(limits.get("duration_minutes", 30))
    radius = int(limits.get("radius_meters", 500))

    origin_lat = dog.last_lat if dog and dog.last_lat else elder.home_lat
    origin_lng = dog.last_lng if dog and dog.last_lng else elder.home_lng

    task = OutingTask(
        elder_id=elder.id,
        decision_id=decision.id,
        dog_device_id=dog.device_id if dog else "",
        watch_device_id=watch.device_id if watch else "",
        status=OutingTaskStatus.ACTIVE,
        started_at=datetime.now(),
        planned_duration_minutes=duration,
        geofence_radius_m=radius,
        origin_lat=origin_lat,
        origin_lng=origin_lng,
        follow_active=True,
        fired_alerts=[],
    )
    db.add(task)
    await db.flush()

    if dog:
        await commands.start_follow(
            dog.device_id, elder.id, task.id, duration, radius
        )
        extra = dict(dog.extra or {})
        extra.update({"follow_mode": True, "task_id": task.id})
        dog.extra = extra
        await db.flush()

    await hub.broadcast(
        "outing_started",
        {
            "task_id": task.id,
            "elder_id": elder.id,
            "duration_minutes": duration,
            "radius_m": radius,
            "origin": {"lat": origin_lat, "lng": origin_lng},
        },
        elder_id=elder.id,
    )
    logger.info("老人 %s 外出任务 #%s 已开始", elder.name, task.id)
    return task


async def record_track_point(
    db: AsyncSession,
    task: OutingTask,
    lat: float,
    lng: float,
    speed: float = 0.0,
    source: str = "dog",
) -> TrackPoint:
    last = await db.execute(
        select(TrackPoint)
        .where(TrackPoint.task_id == task.id)
        .order_by(TrackPoint.id.desc())
        .limit(1)
    )
    last_point = last.scalars().first()
    if last_point:
        task.total_distance_m += haversine_m(
            last_point.lat, last_point.lng, lat, lng
        )
    distance = haversine_m(task.origin_lat, task.origin_lng, lat, lng)
    task.max_distance_m = max(task.max_distance_m, distance)

    point = TrackPoint(
        task_id=task.id,
        elder_id=task.elder_id,
        ts=datetime.now(),
        lat=lat,
        lng=lng,
        speed=speed,
        distance_from_origin_m=round(distance, 1),
        source=source,
    )
    db.add(point)
    await db.flush()
    await hub.broadcast(
        "track_point",
        {
            "task_id": task.id,
            "lat": lat,
            "lng": lng,
            "distance_from_origin_m": round(distance, 1),
            "total_distance_m": round(task.total_distance_m, 1),
            "ts": point.ts,
        },
        elder_id=task.elder_id,
    )
    return point


async def list_track(db: AsyncSession, task_id: int) -> list[TrackPoint]:
    result = await db.execute(
        select(TrackPoint).where(TrackPoint.task_id == task_id).order_by(TrackPoint.id)
    )
    return list(result.scalars().all())


def _build_summary(task: OutingTask, end_reason: str) -> str:
    minutes = int(((task.ended_at or datetime.now()) - task.started_at).total_seconds() // 60)
    reason_text = {
        "voice": "老人语音结束",
        "manual": "后台手动结束",
        "timeout": "超时自动结束",
        "alarm": "因告警中止",
    }.get(end_reason, end_reason or "正常结束")
    return (
        f"本次外出用时 {minutes} 分钟，"
        f"最远离家 {int(task.max_distance_m)} 米，"
        f"累计行走 {int(task.total_distance_m)} 米。"
        f"结束方式：{reason_text}。"
    )


async def end_task(
    db: AsyncSession,
    task: OutingTask,
    end_reason: str = "voice",
    status: str = OutingTaskStatus.COMPLETED,
) -> OutingTask:
    task.ended_at = datetime.now()
    task.status = status
    task.follow_active = False
    task.end_reason = end_reason
    task.summary = _build_summary(task, end_reason)
    await db.flush()

    if task.dog_device_id:
        await commands.stop_follow(task.dog_device_id, task.elder_id, task.id)
        dog = await device_service.get_by_device_id(db, task.dog_device_id)
        if dog:
            extra = dict(dog.extra or {})
            extra.update({"follow_mode": False, "task_id": None})
            dog.extra = extra
            await db.flush()

    await hub.broadcast(
        "outing_ended",
        {
            "task_id": task.id,
            "elder_id": task.elder_id,
            "summary": task.summary,
            "end_reason": end_reason,
            "status": status,
        },
        elder_id=task.elder_id,
    )
    logger.info("外出任务 #%s 结束: %s", task.id, task.summary)
    return task


async def _fire_once(db: AsyncSession, task: OutingTask, key: str) -> bool:
    """同一类提醒每次外出只触发一次。"""
    fired = list(task.fired_alerts or [])
    if key in fired:
        return False
    fired.append(key)
    task.fired_alerts = fired
    await db.flush()
    return True


async def check_active_tasks(db: AsyncSession) -> None:
    """定时巡检: 电子围栏越界、超时未归、跟随丢失。"""
    result = await db.execute(
        select(OutingTask).where(OutingTask.status == OutingTaskStatus.ACTIVE)
    )
    tasks = list(result.scalars().all())
    if not tasks:
        return
    now = datetime.now()

    for task in tasks:
        elapsed = (now - task.started_at).total_seconds() / 60
        planned = task.planned_duration_minutes

        if (
            elapsed >= planned * REMIND_RATIO
            and elapsed < planned
            and await _fire_once(db, task, "remind_return")
        ):
            if task.dog_device_id:
                await commands.speak(
                    task.dog_device_id,
                    await prompt_service.render(
                        db, "outing_remind_return", minutes=int(elapsed)
                    ),
                    elder_id=task.elder_id,
                )

        if elapsed >= planned and await _fire_once(db, task, "timeout"):
            await alarm_service.create_alarm(
                db,
                elder_id=task.elder_id,
                alarm_type=AlarmType.TIMEOUT,
                level=AlarmLevel.WARNING,
                title="外出超时未归",
                description=(
                    f"计划外出 {planned} 分钟，当前已 {int(elapsed)} 分钟仍未结束。"
                ),
                task_id=task.id,
                data={"elapsed_minutes": int(elapsed), "planned_minutes": planned},
            )
            if task.dog_device_id:
                await commands.speak(
                    task.dog_device_id,
                    await prompt_service.get_template(db, "outing_timeout"),
                    elder_id=task.elder_id,
                )

        if elapsed >= planned + 15 and await _fire_once(db, task, "auto_end"):
            await end_task(db, task, end_reason="timeout")
            continue

        if (
            task.max_distance_m > task.geofence_radius_m + GEOFENCE_BUFFER_M
            and await _fire_once(db, task, "geofence")
        ):
            await alarm_service.create_alarm(
                db,
                elder_id=task.elder_id,
                alarm_type=AlarmType.GEOFENCE,
                level=AlarmLevel.WARNING,
                title="超出安全活动范围",
                description=(
                    f"已离家 {int(task.max_distance_m)} 米，"
                    f"超出本次外出 {task.geofence_radius_m} 米的安全范围。"
                ),
                task_id=task.id,
                lat=None,
                lng=None,
                data={
                    "distance_m": int(task.max_distance_m),
                    "radius_m": task.geofence_radius_m,
                },
            )
            if task.dog_device_id:
                await commands.speak(
                    task.dog_device_id,
                    await prompt_service.get_template(db, "outing_geofence"),
                    elder_id=task.elder_id,
                )


async def list_requests(
    db: AsyncSession,
    elder_id: int | None = None,
    decision: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[OutingRequest], int]:
    stmt = select(OutingRequest)
    count_stmt = select(func.count(OutingRequest.id))
    if elder_id is not None:
        stmt = stmt.where(OutingRequest.elder_id == elder_id)
        count_stmt = count_stmt.where(OutingRequest.elder_id == elder_id)
    if decision:
        stmt = stmt.join(OutingDecision).where(OutingDecision.decision == decision)
        count_stmt = count_stmt.join(OutingDecision).where(
            OutingDecision.decision == decision
        )
    total = int((await db.execute(count_stmt)).scalar() or 0)
    stmt = stmt.order_by(OutingRequest.id.desc()).limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars().all()), total


async def list_tasks(
    db: AsyncSession,
    elder_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[OutingTask], int]:
    stmt = select(OutingTask)
    count_stmt = select(func.count(OutingTask.id))
    if elder_id is not None:
        stmt = stmt.where(OutingTask.elder_id == elder_id)
        count_stmt = count_stmt.where(OutingTask.elder_id == elder_id)
    total = int((await db.execute(count_stmt)).scalar() or 0)
    stmt = stmt.order_by(OutingTask.id.desc()).limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars().all()), total
