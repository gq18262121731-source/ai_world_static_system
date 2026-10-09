from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.core.deps import (
    CurrentUser,
    DbSession,
    accessible_elder_ids,
    get_accessible_elder,
)
from app.models import Device, Elder, OutingTask
from app.models.enums import DeviceStatus, DeviceType, OutingTaskStatus
from app.schemas.health import (
    HealthAssessment,
    HealthRecordIn,
    HealthRecordOut,
    HealthScoreOut,
    HealthSummaryOut,
    HealthTrendOut,
    LiveElderVitals,
)
from app.services import health_service

router = APIRouter(prefix="/health", tags=["健康数据"])


@router.get("/live", response_model=list[LiveElderVitals], summary="所有可见老人的实时体征")
async def live(db: DbSession, user: CurrentUser, points: int = 30):
    """首页实时卡片数据源: 一次返回全部可见老人的最新体征与近段采样序列。"""
    allowed = await accessible_elder_ids(db, user)
    stmt = select(Elder).order_by(Elder.id)
    if allowed is not None:
        stmt = stmt.where(Elder.id.in_(allowed or [-1]))
    elders = list((await db.execute(stmt)).scalars().unique().all())
    if not elders:
        return []

    elder_ids = [elder.id for elder in elders]
    devices = list(
        (
            await db.execute(select(Device).where(Device.elder_id.in_(elder_ids)))
        ).scalars().all()
    )
    active_tasks = list(
        (
            await db.execute(
                select(OutingTask).where(
                    OutingTask.elder_id.in_(elder_ids),
                    OutingTask.status == OutingTaskStatus.ACTIVE,
                )
            )
        ).scalars().all()
    )
    task_by_elder = {task.elder_id: task for task in active_tasks}

    snapshots = []
    for elder in elders:
        snapshot = await health_service.live_snapshot(db, elder, points)
        owned = [d for d in devices if d.elder_id == elder.id]
        watch = next((d for d in owned if d.device_type == DeviceType.WATCH), None)
        dog = next((d for d in owned if d.device_type == DeviceType.DOG), None)
        snapshot.watch_online = bool(watch and watch.status == DeviceStatus.ONLINE)
        snapshot.dog_online = bool(dog and dog.status == DeviceStatus.ONLINE)
        snapshot.dog_following = bool(dog and (dog.extra or {}).get("follow_mode"))
        task = task_by_elder.get(elder.id)
        snapshot.active_task_id = task.id if task else None
        snapshots.append(snapshot)
    return snapshots


@router.get("/records", response_model=list[HealthRecordOut], summary="健康遥测记录")
async def list_records(
    elder_id: int,
    db: DbSession,
    user: CurrentUser,
    hours: int = 24,
    limit: int = 500,
):
    await get_accessible_elder(db, user, elder_id)
    records = await health_service.list_records(
        db, elder_id, start=datetime.now() - timedelta(hours=hours), limit=limit
    )
    return [HealthRecordOut.model_validate(r) for r in records]


@router.get("/trend", response_model=HealthTrendOut, summary="单项体征的折线数据（分桶，剔除疑似坏读数）")
async def trend(
    elder_id: int,
    db: DbSession,
    user: CurrentUser,
    metric: str = "heart_rate",
    hours: int = 24,
    points: int = 240,
):
    """老人档案页大折线图的数据源: 窗口内按等宽时间桶给出 avg/min/max, 点数可控。"""
    await get_accessible_elder(db, user, elder_id)
    try:
        return await health_service.build_trend(db, elder_id, metric, hours, points)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/latest", response_model=HealthRecordOut | None, summary="最新一条健康数据")
async def latest(elder_id: int, db: DbSession, user: CurrentUser):
    await get_accessible_elder(db, user, elder_id)
    record = await health_service.latest_record(db, elder_id)
    return HealthRecordOut.model_validate(record) if record else None


@router.get("/assessment", response_model=HealthAssessment, summary="健康快照与异常判定")
async def assessment(
    elder_id: int, db: DbSession, user: CurrentUser, window_hours: int = 24
):
    elder = await get_accessible_elder(db, user, elder_id)
    return await health_service.build_assessment(db, elder, window_hours)


@router.get("/score", response_model=HealthScoreOut, summary="健康评分（0~100，带维度明细与扣分原因）")
async def score(elder_id: int, db: DbSession, user: CurrentUser):
    elder = await get_accessible_elder(db, user, elder_id)
    assessment = await health_service.build_assessment(db, elder, 24)
    # 读请求不提交，评估顺带写的日摘要分数会随会话丢弃，不影响结果
    return assessment.health_score or HealthScoreOut(summary="暂无足够数据评分")


@router.get("/summaries", response_model=list[HealthSummaryOut], summary="健康日摘要与趋势")
async def summaries(elder_id: int, db: DbSession, user: CurrentUser, days: int = 7):
    await get_accessible_elder(db, user, elder_id)
    rows = await health_service.list_summaries(db, elder_id, days)
    return [HealthSummaryOut.model_validate(r) for r in rows]


@router.post("/records", response_model=HealthRecordOut, summary="上报健康数据(设备/模拟器)")
async def ingest(elder_id: int, payload: HealthRecordIn, db: DbSession, user: CurrentUser):
    elder = await get_accessible_elder(db, user, elder_id)
    record = await health_service.ingest(db, elder_id, payload)
    await health_service.rebuild_daily_summary(db, elder)
    await db.commit()
    await db.refresh(record)
    return HealthRecordOut.model_validate(record)


@router.post("/summaries/rebuild", response_model=HealthSummaryOut | None, summary="重算日摘要")
async def rebuild(elder_id: int, db: DbSession, user: CurrentUser):
    elder = await get_accessible_elder(db, user, elder_id)
    summary = await health_service.rebuild_daily_summary(db, elder)
    await db.commit()
    return HealthSummaryOut.model_validate(summary) if summary else None
