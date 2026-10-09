import asyncio

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.agent import orchestrator
from app.core.deps import AdminUser, CurrentUser, DbSession, get_accessible_elder
from app.schemas.agent import AgentReply
from app.services import simulator_service
from app.services.demo_runtime import demo_runtime

router = APIRouter(prefix="/simulator", tags=["设备模拟器"])


class RuntimeStartRequest(BaseModel):
    # 留空则两位演示老人的手表和机器狗一起跑；LJG / ZXL 只跑其中一位
    only: str = ""
    watch_interval: float | None = Field(None, ge=1, le=120)


class ProfileRequest(BaseModel):
    elder_id: int
    profile: str = "normal"
    points: int = Field(6, ge=1, le=48)


class DeviceStateRequest(BaseModel):
    elder_id: int
    device_type: str = "watch"
    battery: int | None = None
    online: bool | None = None


class FallRequest(BaseModel):
    elder_id: int
    source: str = "dog"
    confidence: float = 0.9


class SpeakRequest(BaseModel):
    elder_id: int
    text: str


class WalkRequest(BaseModel):
    elder_id: int
    steps: int = Field(5, ge=1, le=50)
    meters_per_step: float = Field(60.0, ge=5, le=500)


class ResetRequest(BaseModel):
    elder_id: int
    clear_health: bool = False


@router.get("/runtime", summary="托管的模拟器进程状态")
async def runtime_status(_: CurrentUser, log_lines: int = 0):
    result = demo_runtime.status()
    if log_lines:
        result["log"] = demo_runtime.log_tail(log_lines)
    return result


@router.post("/runtime/start", summary="启动模拟器进程（演示模式揭示数据时调用）")
async def runtime_start(payload: RuntimeStartRequest, _: CurrentUser):
    return await asyncio.to_thread(demo_runtime.start, payload.only, payload.watch_interval)


@router.post("/runtime/stop", summary="停止托管的模拟器进程")
async def runtime_stop(_: CurrentUser):
    # 正常停止要等模拟器发完 offline，最长几秒，不能卡住事件循环
    return await asyncio.to_thread(demo_runtime.stop)


@router.get("/state", summary="模拟器与设备当前状态")
async def state(elder_id: int, db: DbSession, user: CurrentUser):
    elder = await get_accessible_elder(db, user, elder_id)
    return await simulator_service.state(db, elder)


@router.post("/health-profile", summary="注入指定档位的健康数据")
async def health_profile(payload: ProfileRequest, db: DbSession, user: CurrentUser):
    elder = await get_accessible_elder(db, user, payload.elder_id)
    result = await simulator_service.apply_health_profile(
        db, elder, payload.profile, payload.points
    )
    await db.commit()
    return result


@router.post("/device-state", summary="调整设备电量/在线状态")
async def device_state(payload: DeviceStateRequest, db: DbSession, user: CurrentUser):
    elder = await get_accessible_elder(db, user, payload.elder_id)
    result: dict = {}
    if payload.battery is not None:
        result |= await simulator_service.set_battery(
            db, elder, payload.device_type, payload.battery
        )
    if payload.online is not None:
        result |= await simulator_service.set_online(
            db, elder, payload.device_type, payload.online
        )
    await db.commit()
    return result or {"ok": False, "message": "未指定要修改的状态"}


@router.post("/fall", summary="触发跌倒事件")
async def fall(payload: FallRequest, db: DbSession, user: CurrentUser):
    elder = await get_accessible_elder(db, user, payload.elder_id)
    result = await simulator_service.trigger_fall(
        db, elder, payload.source, payload.confidence
    )
    await db.commit()
    return result


@router.post("/speak", response_model=AgentReply, summary="模拟老人对机器狗说话")
async def speak(payload: SpeakRequest, db: DbSession, user: CurrentUser):
    elder = await get_accessible_elder(db, user, payload.elder_id)
    reply = await orchestrator.handle_utterance(db, elder, payload.text)
    await db.commit()
    return reply


@router.post("/walk", summary="模拟外出行走(生成轨迹)")
async def walk(payload: WalkRequest, db: DbSession, user: CurrentUser):
    elder = await get_accessible_elder(db, user, payload.elder_id)
    result = await simulator_service.walk(
        db, elder, payload.steps, payload.meters_per_step
    )
    await db.commit()
    return result


@router.post("/reset", summary="重置演示数据(清空外出/告警/对话记录)")
async def reset(payload: ResetRequest, db: DbSession, user: CurrentUser):
    elder = await get_accessible_elder(db, user, payload.elder_id)
    result = await simulator_service.reset_demo_state(db, elder, payload.clear_health)
    await db.commit()
    return result


@router.post("/demo-baseline", summary="一键恢复四位老人的演示基线")
async def demo_baseline(db: DbSession, _: AdminUser):
    result = await simulator_service.restore_demo_baselines(db)
    await db.commit()
    return result
