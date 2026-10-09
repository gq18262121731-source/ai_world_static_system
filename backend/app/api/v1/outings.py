from fastapi import APIRouter, HTTPException, Request

from app.core.deps import AdminUser, CurrentUser, DbSession, get_accessible_elder
from app.models import OutingDecision, OutingTask
from app.models.enums import OutingDecisionType, OutingTaskStatus
from app.schemas.common import Msg
from app.schemas.outing import (
    ManualOutingRequest,
    OutingDecisionOut,
    OutingRequestOut,
    OutingStats,
    OutingTaskOut,
    RuleReport,
    TrackPointOut,
)
from app.services import audit_service, evaluation_service, outing_service

router = APIRouter(prefix="/outings", tags=["外出评估与任务"])


@router.get("/stats", response_model=OutingStats, summary="外出统计")
async def stats(elder_id: int, db: DbSession, user: CurrentUser):
    await get_accessible_elder(db, user, elder_id)
    return await outing_service.get_stats(db, elder_id)


@router.post("/evaluate", summary="发起外出评估(不落库, 仅预演)")
async def evaluate_preview(
    payload: ManualOutingRequest, db: DbSession, user: CurrentUser
):
    elder = await get_accessible_elder(db, user, payload.elder_id)
    report, evaluation, snapshots = await evaluation_service.evaluate_outing(
        db, elder, payload.text
    )
    return {
        "rule_report": report.model_dump(mode="json"),
        "evaluation": evaluation.model_dump(mode="json"),
        "snapshots": snapshots,
    }


@router.post("/request", summary="发起外出申请(落库并按结果开启跟随)")
async def create_request(
    payload: ManualOutingRequest,
    db: DbSession,
    user: CurrentUser,
    request: Request,
):
    elder = await get_accessible_elder(db, user, payload.elder_id)
    outing_request, decision, report, evaluation = await evaluation_service.evaluate_and_persist(
        db, elder, payload.text, channel=payload.channel
    )
    task = None
    if decision.decision != OutingDecisionType.DENY:
        task = await outing_service.start_task(db, elder, decision)
    await audit_service.record(
        db,
        user,
        "outing_request",
        "elder",
        elder.id,
        {"decision": decision.decision, "text": payload.text},
        request,
    )
    await db.commit()
    return {
        "request_id": outing_request.id,
        "decision": OutingDecisionOut.model_validate(decision).model_dump(mode="json"),
        "rule_report": report.model_dump(mode="json"),
        "speech_text": evaluation.speech_text,
        "task_id": task.id if task else None,
    }


@router.get("/requests", summary="外出申请与决策日志")
async def list_requests(
    db: DbSession,
    user: CurrentUser,
    elder_id: int | None = None,
    decision: str | None = None,
    page: int = 1,
    page_size: int = 20,
):
    if elder_id is not None:
        await get_accessible_elder(db, user, elder_id)
    items, total = await outing_service.list_requests(
        db, elder_id, decision, limit=page_size, offset=(page - 1) * page_size
    )
    return {
        "items": [
            OutingRequestOut.model_validate(item).model_dump(mode="json")
            for item in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/decisions/{decision_id}", response_model=OutingDecisionOut, summary="决策详情")
async def get_decision(decision_id: int, db: DbSession, user: CurrentUser):
    decision = await db.get(OutingDecision, decision_id)
    if not decision:
        raise HTTPException(status_code=404, detail="决策记录不存在")
    await get_accessible_elder(db, user, decision.elder_id)
    return OutingDecisionOut.model_validate(decision)


@router.get("/tasks", summary="外出任务列表")
async def list_tasks(
    db: DbSession,
    user: CurrentUser,
    elder_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
):
    if elder_id is not None:
        await get_accessible_elder(db, user, elder_id)
    items, total = await outing_service.list_tasks(
        db, elder_id, limit=page_size, offset=(page - 1) * page_size
    )
    return {
        "items": [OutingTaskOut.model_validate(t).model_dump(mode="json") for t in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/tasks/active", response_model=OutingTaskOut | None, summary="进行中的外出任务")
async def active_task(elder_id: int, db: DbSession, user: CurrentUser):
    await get_accessible_elder(db, user, elder_id)
    task = await outing_service.get_active_task(db, elder_id)
    return OutingTaskOut.model_validate(task) if task else None


@router.get("/tasks/{task_id}/track", response_model=list[TrackPointOut], summary="外出轨迹")
async def track(task_id: int, db: DbSession, user: CurrentUser):
    task = await db.get(OutingTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="外出任务不存在")
    await get_accessible_elder(db, user, task.elder_id)
    points = await outing_service.list_track(db, task_id)
    return [TrackPointOut.model_validate(p) for p in points]


@router.post("/tasks/{task_id}/end", response_model=OutingTaskOut, summary="结束外出任务")
async def end_task(
    task_id: int, db: DbSession, operator: AdminUser, request: Request, reason: str = "manual"
):
    task = await db.get(OutingTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="外出任务不存在")
    if task.status != OutingTaskStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="该任务已结束")
    await outing_service.end_task(db, task, end_reason=reason)
    await audit_service.record(
        db, operator, "end_outing_task", "task", task_id, {"reason": reason}, request
    )
    await db.commit()
    await db.refresh(task)
    return OutingTaskOut.model_validate(task)


@router.get("/rule-report", response_model=RuleReport, summary="仅查看规则引擎判定结果")
async def rule_report(elder_id: int, db: DbSession, user: CurrentUser):
    from app.rules.engine import evaluate_rules
    from app.services import device_service, health_service, weather_service

    elder = await get_accessible_elder(db, user, elder_id)
    health = await health_service.build_assessment(db, elder)
    stats = await outing_service.get_stats(db, elder_id)
    weather = await weather_service.get_weather(elder.city, elder.home_lat, elder.home_lng)
    devices = await device_service.device_status_snapshot(db, elder_id)
    report, _ = await evaluate_rules(db, elder, health, stats, weather, devices)
    return report


@router.post("/tasks/{task_id}/track", response_model=Msg, summary="上报轨迹点(设备/模拟器)")
async def push_track(
    task_id: int, lat: float, lng: float, db: DbSession, user: CurrentUser, speed: float = 0.0
):
    task = await db.get(OutingTask, task_id)
    if not task or task.status != OutingTaskStatus.ACTIVE:
        raise HTTPException(status_code=404, detail="没有进行中的外出任务")
    await get_accessible_elder(db, user, task.elder_id)
    point = await outing_service.record_track_point(db, task, lat, lng, speed)
    await db.commit()
    return Msg(
        message="已记录",
        data={"distance_from_origin_m": point.distance_from_origin_m},
    )
