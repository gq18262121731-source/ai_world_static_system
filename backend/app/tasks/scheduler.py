import asyncio
import time

from sqlalchemy import select

from app.core.config import settings
from app.core.db import session_scope
from app.core.logging import get_logger
from app.models import Elder
from app.services import (
    alarm_service,
    device_service,
    health_service,
    outing_service,
    simulator_service,
)

logger = get_logger(__name__)

# 健康评分平时随每次上报刷新；手环不在身上、没有数据进来时，"最近 10 分钟没有读数"
# 这类变化也会改变分数，所以定期重算一遍，首页读到的分数才不会停在几小时前
SCORE_REFRESH_SECONDS = 300
_last_score_refresh = 0.0


async def _refresh_scores(db) -> None:
    elders = list((await db.execute(select(Elder).order_by(Elder.id))).scalars().all())
    for elder in elders:
        try:
            await health_service.build_assessment(db, elder, 24)
        except Exception:
            logger.exception("刷新老人 %s 的健康评分失败", elder.id)


async def _tick() -> None:
    global _last_score_refresh
    async with session_scope() as db:
        await device_service.mark_offline_devices(db)
        await outing_service.check_active_tasks(db)
        await alarm_service.check_pending_confirmations(db)
        await alarm_service.escalate_unhandled(db)
        await simulator_service.keep_simulated_data_fresh(db)
        if time.monotonic() - _last_score_refresh >= SCORE_REFRESH_SECONDS:
            _last_score_refresh = time.monotonic()
            await _refresh_scores(db)


async def run_scheduler(stop_event: asyncio.Event) -> None:
    """后台巡检: 设备离线、外出围栏与超时、跌倒确认超时、告警升级、健康评分刷新。"""
    logger.info("后台巡检任务已启动, 间隔 %d 秒", settings.scheduler_interval_seconds)
    while not stop_event.is_set():
        try:
            await _tick()
        except Exception:
            logger.exception("后台巡检执行失败")
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=settings.scheduler_interval_seconds
            )
        except TimeoutError:
            continue
    logger.info("后台巡检任务已停止")
