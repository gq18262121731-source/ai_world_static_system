import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1 import api_router
from app.api.v1 import dog_audio as dog_audio_router
from app.api.v1 import ws as ws_router
from app.core.cache import cache
from app.core.config import settings
from app.core.db import add_missing_columns, engine, session_scope
from app.core.events import hub
from app.core.logging import get_logger, setup_logging
from app.iot.dispatcher import handle_message
from app.iot.mqtt_client import mqtt_bridge
from app.models import Base
from app.services.demo_runtime import demo_runtime
from app.services.simulator_service import restore_demo_baselines
from app.tasks.scheduler import run_scheduler

setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("正在启动 %s", settings.app_name)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        added = await conn.run_sync(add_missing_columns, Base.metadata)
        if added:
            logger.info("已为既有表补充新列: %s", ", ".join(added))

    await cache.connect()

    mqtt_bridge.set_handler(handle_message)
    await mqtt_bridge.start(asyncio.get_running_loop())

    # 每次服务启动都回到稳定、可直接展示的四人基线。之后在设备模拟器中的
    # 手动调整会继续生效，直到主动恢复基线或下次重启服务。
    async with session_scope() as db:
        baseline = await restore_demo_baselines(db)
        if baseline["restored"]:
            logger.info("演示基线就绪: %d 位老人", len(baseline["restored"]))

    stop_event = asyncio.Event()
    scheduler_task = asyncio.create_task(run_scheduler(stop_event))

    logger.info("启动完成: 数据库=%s, 缓存=%s", settings.database_url.split("@")[-1], cache.backend)
    yield

    stop_event.set()
    try:
        await asyncio.wait_for(scheduler_task, timeout=5)
    except (TimeoutError, asyncio.CancelledError):
        scheduler_task.cancel()
    # 演示模式拉起来的模拟器跟着后端一起退，别留下孤儿进程继续往 MQTT 发数据
    if demo_runtime.running:
        await asyncio.to_thread(demo_runtime.stop)
    await mqtt_bridge.stop()
    await cache.close()
    await engine.dispose()
    logger.info("服务已停止")


app = FastAPI(
    title=settings.app_name,
    description=(
        "面向独居老人外出场景的 AIoT 养老平台：健康手表数据接入、机器狗语音交互、"
        "外出可行性智能体评估、跟随任务与电子围栏、跌倒检测融合告警。"
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api_prefix)
app.include_router(ws_router.router)
app.include_router(dog_audio_router.router)
app.mount("/media", StaticFiles(directory=str(settings.media_dir)), name="media")


@app.get("/", tags=["系统"], summary="服务信息")
async def root():
    return {
        "name": settings.app_name,
        "version": "1.0.0",
        "docs": "/docs",
        "api_prefix": settings.api_prefix,
    }


@app.get("/health", tags=["系统"], summary="健康检查")
async def healthcheck():
    return {
        "status": "ok",
        "cache": cache.backend,
        "mqtt": mqtt_bridge.status(),
        "websocket_clients": hub.online,
    }
