from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import session_scope
from app.core.logging import get_logger
from app.iot import probe, topics
from app.iot.gateways import gateway_registry
from app.models import Elder
from app.models.enums import AlarmLevel, AlarmType, DeviceStatus, DeviceType
from app.schemas.health import HealthRecordIn
from app.services import (
    alarm_service,
    device_service,
    health_score,
    health_service,
    outing_service,
)

logger = get_logger(__name__)


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


async def handle_message(topic: str, payload: dict) -> None:
    parsed = topics.parse(topic)
    if not parsed:
        return
    device_type, device_id, kind = parsed

    # 网关不是绑定老人的设备，单独登记，不能走下面的设备流程
    if device_type == "gateway":
        if kind == topics.STATUS:
            gateway_registry.update(device_id, payload)
        return

    async with session_scope() as db:
        device = await device_service.ensure_device(db, device_id, device_type)
        if kind == topics.STATUS:
            await _handle_status(db, device_id, payload)
            return
        # 探活回应要在绑定检查之前处理：没绑定老人的设备也应该能响应自检
        if kind == topics.EVENT and payload.get("event") == "pong":
            probe.resolve(str(payload.get("nonce", "")), payload)
            await device_service.touch(db, device, battery=payload.get("battery"))
            return
        if device.elder_id is None:
            logger.warning("设备 %s 尚未绑定老人, 忽略上报", device_id)
            return
        elder = await db.get(Elder, device.elder_id)
        if elder is None:
            return

        if device_type == DeviceType.WATCH and kind == topics.TELEMETRY:
            await _handle_watch_telemetry(db, elder, device_id, payload)
        elif device_type == DeviceType.WATCH and kind == topics.EVENT:
            await _handle_watch_event(db, elder, device_id, payload)
        elif device_type == DeviceType.DOG and kind == topics.TELEMETRY:
            await _handle_dog_telemetry(db, elder, device_id, payload)
        elif device_type == DeviceType.DOG and kind == topics.EVENT:
            await _handle_dog_event(db, elder, device_id, payload)
        elif device_type == DeviceType.DOG and kind == topics.SPEECH:
            await _handle_dog_speech(db, elder, device_id, payload)


async def _handle_status(db: AsyncSession, device_id: str, payload: dict) -> None:
    device = await device_service.get_by_device_id(db, device_id)
    if not device:
        return
    online = str(payload.get("status", "online")) == "online"
    override = (device.extra or {}).get("simulator_online_override")
    if override is not None:
        online = bool(override)
    device.status = DeviceStatus.ONLINE if online else DeviceStatus.OFFLINE
    if online:
        device.last_seen_at = datetime.now()
    await db.flush()
    logger.info("设备 %s 状态: %s", device_id, device.status)


async def _handle_watch_telemetry(
    db: AsyncSession, elder: Elder, device_id: str, payload: dict
) -> None:
    device = await device_service.get_by_device_id(db, device_id)
    if device:
        await device_service.touch(
            db,
            device,
            battery=payload.get("battery"),
            lat=payload.get("lat"),
            lng=payload.get("lng"),
        )
    data = HealthRecordIn(
        device_id=device_id,
        recorded_at=_parse_ts(payload.get("ts")),
        heart_rate=payload.get("heart_rate"),
        systolic=payload.get("systolic"),
        diastolic=payload.get("diastolic"),
        spo2=payload.get("spo2"),
        temperature=payload.get("temperature"),
        steps=payload.get("steps"),
        sleep_hours=payload.get("sleep_hours"),
        activity_level=payload.get("activity_level", "rest"),
        battery=payload.get("battery"),
        lat=payload.get("lat"),
        lng=payload.get("lng"),
        # 真机网关会带 source=serial；演示模拟器带 simulator。
        # 两者必须能区分，否则模拟数据会掩盖真实设备的断流
        source=payload.get("source", "watch"),
    )
    await health_service.ingest(db, elder.id, data)
    await health_service.rebuild_daily_summary(db, elder)

    # 告警看最近一小时的状态；健康评分另按 24 小时口径刷新，且按老人节流
    assessment = await health_service.build_assessment(
        db, elder, window_hours=1, with_score=False
    )
    if assessment.abnormal_items:
        await alarm_service.check_health_anomaly(
            db, elder.id, assessment.abnormal_items, assessment.risk_score
        )
    await health_score.refresh(db, elder)

    task = await outing_service.get_active_task(db, elder.id)
    if task and payload.get("lat") and payload.get("lng") and not task.dog_device_id:
        await outing_service.record_track_point(
            db, task, payload["lat"], payload["lng"], source="watch"
        )


async def _handle_watch_event(
    db: AsyncSession, elder: Elder, device_id: str, payload: dict
) -> None:
    event = payload.get("event", "")
    if event == "fall":
        await alarm_service.handle_fall_event(
            db,
            elder_id=elder.id,
            source="watch",
            device_id=device_id,
            confidence=float(payload.get("confidence", 0.9)),
            lat=payload.get("lat"),
            lng=payload.get("lng"),
            detected_at=_parse_ts(payload.get("ts")),
            raw=payload,
        )
    elif event == "sos":
        await alarm_service.trigger_sos(
            db, elder.id, "老人通过手表按下 SOS 按键", payload.get("lat"), payload.get("lng")
        )
    elif event == "low_battery":
        await alarm_service.create_alarm(
            db,
            elder_id=elder.id,
            alarm_type=AlarmType.DEVICE_OFFLINE,
            level=AlarmLevel.INFO,
            title="手表电量不足",
            description=f"手表电量仅剩 {payload.get('battery', '?')}%，请及时充电。",
            data=payload,
            notify=False,
        )


async def _handle_dog_telemetry(
    db: AsyncSession, elder: Elder, device_id: str, payload: dict
) -> None:
    device = await device_service.get_by_device_id(db, device_id)
    if device:
        await device_service.touch(
            db,
            device,
            battery=payload.get("battery"),
            lat=payload.get("lat"),
            lng=payload.get("lng"),
            extra={
                "follow_mode": payload.get("follow_mode", False),
                "gait": payload.get("gait", "idle"),
            },
        )
    task = await outing_service.get_active_task(db, elder.id)
    if task and payload.get("lat") is not None and payload.get("lng") is not None:
        await outing_service.record_track_point(
            db,
            task,
            float(payload["lat"]),
            float(payload["lng"]),
            speed=float(payload.get("speed", 0.0)),
            source="dog",
        )


async def _handle_dog_event(
    db: AsyncSession, elder: Elder, device_id: str, payload: dict
) -> None:
    event = payload.get("event", "")
    if event == "fall_detected":
        await alarm_service.handle_fall_event(
            db,
            elder_id=elder.id,
            source="dog",
            device_id=device_id,
            confidence=float(payload.get("confidence", 0.85)),
            lat=payload.get("lat"),
            lng=payload.get("lng"),
            detected_at=_parse_ts(payload.get("ts")),
            raw=payload,
        )
    elif event == "follow_lost":
        task = await outing_service.get_active_task(db, elder.id)
        await alarm_service.create_alarm(
            db,
            elder_id=elder.id,
            alarm_type=AlarmType.FOLLOW_LOST,
            level=AlarmLevel.WARNING,
            title="机器狗跟丢老人",
            description=payload.get("detail", "机器狗在跟随过程中丢失了老人目标。"),
            task_id=task.id if task else None,
            lat=payload.get("lat"),
            lng=payload.get("lng"),
            data=payload,
        )


async def _handle_dog_speech(
    db: AsyncSession, elder: Elder, device_id: str, payload: dict
) -> None:
    text = str(payload.get("text", "")).strip()
    if not text:
        return
    from app.agent import orchestrator

    await orchestrator.handle_utterance(db, elder, text, device_id=device_id)
