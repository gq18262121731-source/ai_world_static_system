"""设备模拟器的服务端支撑。

前端模拟器面板的操作统一走这里:
1. 优先通过 MQTT 控制主题下发给独立运行的模拟器进程(最接近真机链路);
2. Broker 不可用时直接在后端产生等效效果, 保证没有 Docker 也能演示完整闭环。
"""

import random
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.geo import haversine_m
from app.core.logging import get_logger
from app.iot import commands
from app.iot.mqtt_client import mqtt_bridge
from app.models import Device, Elder, HealthRecord
from app.models.enums import DeviceStatus, DeviceType
from app.schemas.health import HealthRecordIn
from app.services import alarm_service, device_service, health_service, outing_service

logger = get_logger(__name__)

# 演示用的健康状态档位: 覆盖"可以外出"与"不可外出"的关键分支
HEALTH_PROFILES: dict[str, dict] = {
    "normal": {
        "label": "健康平稳",
        "heart_rate": (68, 82),
        "systolic": (118, 132),
        "diastolic": (72, 84),
        "spo2": (96, 99),
        "temperature": (36.3, 36.9),
        "sleep_hours": (6.5, 8.0),
    },
    "tired": {
        "label": "疲劳(睡眠不足)",
        "heart_rate": (78, 92),
        "systolic": (128, 140),
        "diastolic": (80, 90),
        "spo2": (94, 96),
        "temperature": (36.4, 37.0),
        "sleep_hours": (3.5, 5.0),
    },
    "hypoxia": {
        "label": "血氧偏低",
        "heart_rate": (88, 102),
        "systolic": (124, 138),
        "diastolic": (78, 88),
        "spo2": (85, 89),
        "temperature": (36.4, 36.9),
        "sleep_hours": (5.0, 7.0),
    },
    "hypertension": {
        "label": "血压偏高",
        "heart_rate": (76, 90),
        "systolic": (168, 182),
        "diastolic": (104, 112),
        "spo2": (95, 98),
        "temperature": (36.3, 36.8),
        "sleep_hours": (5.0, 7.0),
    },
    "tachycardia": {
        "label": "心率过快",
        "heart_rate": (124, 142),
        "systolic": (138, 150),
        "diastolic": (86, 96),
        "spo2": (93, 96),
        "temperature": (36.6, 37.2),
        "sleep_hours": (5.0, 7.0),
    },
    "fever": {
        "label": "发热",
        "heart_rate": (95, 110),
        "systolic": (126, 140),
        "diastolic": (80, 90),
        "spo2": (94, 97),
        "temperature": (38.2, 38.9),
        "sleep_hours": (4.5, 6.5),
    },
    "hypertension_tachycardia": {
        "label": "血压偏高、心率过快",
        "heart_rate": (126, 140),
        "systolic": (168, 182),
        "diastolic": (106, 112),
        "spo2": (95, 98),
        "temperature": (36.4, 36.9),
        "sleep_hours": (6.0, 7.5),
    },
    "tired_hypoxia_hypertension": {
        "label": "疲劳缺觉、血氧偏低、血压偏高",
        "heart_rate": (82, 96),
        "systolic": (168, 182),
        "diastolic": (106, 112),
        "spo2": (85, 89),
        "temperature": (36.4, 36.9),
        "sleep_hours": (3.5, 4.8),
    },
    "fever_hypoxia_hypertension": {
        "label": "发热、血氧偏低、血压偏高",
        "heart_rate": (88, 98),
        "systolic": (168, 182),
        "diastolic": (106, 112),
        "spo2": (85, 89),
        "temperature": (38.2, 38.9),
        "sleep_hours": (6.0, 7.5),
    },
}

# 四位主展示老人的稳定基线：三位健康平稳、一位异常。档位会同时写入设备 extra，后台巡检据此续写
# 新鲜的模拟读数；用户在设备模拟器里切换档位后，新档位会替代这里的默认值。
DEMO_BASELINES: dict[str, dict] = {
    "张三": {"profile": "normal", "watch_online": True, "dog_online": True},
    "李四": {
        "profile": "hypertension_tachycardia",
        "watch_online": True,
        "dog_online": True,
    },
    "王五": {
        "profile": "normal",
        "watch_online": True,
        "dog_online": False,
    },
    "陈二": {
        "profile": "normal",
        "watch_online": True,
        "dog_online": False,
    },
}

DEMO_DEVICE_IDS: dict[str, dict[str, str]] = {
    "张三": {"watch": "WATCH-LJG-001", "dog": "DOG-LJG-001"},
    "李四": {"watch": "WATCH-ZXL-001", "dog": "DOG-ZXL-001"},
    "王五": {"watch": "WATCH-WW-001", "dog": "DOG-WW-001"},
    "陈二": {"watch": "WATCH-CGF-001", "dog": "DOG-CGF-001"},
}

SIMULATOR_PROFILE_KEY = "simulator_profile"
SIMULATOR_ONLINE_OVERRIDE_KEY = "simulator_online_override"
DEMO_FRESH_SECONDS = 60


def _pick(profile: dict, key: str) -> float:
    low, high = profile[key]
    value = random.uniform(low, high)
    return round(value, 1) if isinstance(low, float) else round(value)


def generate_vitals(profile_key: str) -> dict:
    profile = HEALTH_PROFILES.get(profile_key, HEALTH_PROFILES["normal"])
    return {
        "heart_rate": int(_pick(profile, "heart_rate")),
        "systolic": int(_pick(profile, "systolic")),
        "diastolic": int(_pick(profile, "diastolic")),
        "spo2": int(_pick(profile, "spo2")),
        "temperature": round(_pick(profile, "temperature"), 1),
        "sleep_hours": round(_pick(profile, "sleep_hours"), 1),
    }


async def _watch_device(db: AsyncSession, elder_id: int):
    return await device_service.get_elder_device(db, elder_id, DeviceType.WATCH)


async def _dog_device(db: AsyncSession, elder_id: int):
    return await device_service.get_elder_device(db, elder_id, DeviceType.DOG)


async def apply_health_profile(
    db: AsyncSession,
    elder: Elder,
    profile_key: str,
    points: int = 6,
    *,
    check_alarm: bool = True,
) -> dict:
    """按档位注入一段健康数据, 让规则引擎立刻能取到对应结论。"""
    watch = await _watch_device(db, elder.id)
    device_id = watch.device_id if watch else ""
    delivered = False
    if watch:
        extra = dict(watch.extra or {})
        extra[SIMULATOR_PROFILE_KEY] = (
            profile_key if profile_key in HEALTH_PROFILES else "normal"
        )
        watch.extra = extra
        if mqtt_bridge.connected:
            delivered = commands.send_simulator_control(
                device_id, "set_profile", {"profile": profile_key}
            )

    now = datetime.now()
    base_steps = random.randint(800, 3200)
    generated = []
    for index in range(points):
        vitals = generate_vitals(profile_key)
        record = HealthRecordIn(
            device_id=device_id,
            recorded_at=now - timedelta(minutes=(points - index - 1) * 5),
            steps=base_steps + index * random.randint(20, 90),
            activity_level="rest",
            battery=watch.battery if watch else 90,
            lat=elder.home_lat,
            lng=elder.home_lng,
            # 演示数据单独标记，避免和真机 serial 数据混为一谈
            source="simulator",
            **vitals,
        )
        await health_service.ingest(db, elder.id, record)
        generated.append(vitals)
    await health_service.rebuild_daily_summary(db, elder)

    if watch:
        await device_service.touch(db, watch, battery=watch.battery)

    assessment = await health_service.build_assessment(
        db, elder, window_hours=2, with_score=False
    )
    if check_alarm and assessment.abnormal_items:
        await alarm_service.check_health_anomaly(
            db, elder.id, assessment.abnormal_items, assessment.risk_score
        )
    # 演示时点了档位就要立刻看到评分变化，不走节流
    from app.services import health_score

    await health_score.refresh(db, elder, force=True)
    logger.info(
        "模拟器为 %s 注入 %s 档位健康数据 %d 条(mqtt=%s)",
        elder.name,
        profile_key,
        points,
        delivered,
    )
    return {
        "profile": profile_key,
        "label": HEALTH_PROFILES.get(profile_key, {}).get("label", profile_key),
        "generated": generated[-1] if generated else {},
        "delivered_via_mqtt": delivered,
        "abnormal_items": assessment.abnormal_items,
        "risk_score": assessment.risk_score,
    }


async def set_battery(
    db: AsyncSession, elder: Elder, device_type: str, battery: int
) -> dict:
    device = await device_service.get_elder_device(db, elder.id, device_type)
    if not device:
        return {"ok": False, "message": f"未找到{device_type}设备"}
    device.battery = max(0, min(100, battery))
    await db.flush()
    if mqtt_bridge.connected:
        commands.send_simulator_control(
            device.device_id, "set_battery", {"battery": device.battery}
        )
    return {"ok": True, "device_id": device.device_id, "battery": device.battery}


async def set_online(
    db: AsyncSession, elder: Elder, device_type: str, online: bool
) -> dict:
    device = await device_service.get_elder_device(db, elder.id, device_type)
    if not device:
        return {"ok": False, "message": f"未找到{device_type}设备"}
    device.status = DeviceStatus.ONLINE if online else DeviceStatus.OFFLINE
    extra = dict(device.extra or {})
    extra[SIMULATOR_ONLINE_OVERRIDE_KEY] = online
    device.extra = extra
    if online:
        device.last_seen_at = datetime.now()
    await db.flush()
    if mqtt_bridge.connected:
        commands.send_simulator_control(
            device.device_id, "set_online", {"online": online}
        )
    return {"ok": True, "device_id": device.device_id, "status": device.status}


async def _ensure_demo_device(
    db: AsyncSession, elder: Elder, device_type: str
) -> Device:
    device = await device_service.get_elder_device(db, elder.id, device_type)
    if device:
        return device
    device_id = DEMO_DEVICE_IDS[elder.name][device_type]
    device = await device_service.ensure_device(
        db,
        device_id,
        device_type,
        elder_id=elder.id,
        name=(
            f"{elder.name}的健康手表"
            if device_type == DeviceType.WATCH
            else f"{elder.name}的陪护机器狗"
        ),
    )
    device.battery = 90
    device.last_lat = elder.home_lat
    device.last_lng = elder.home_lng
    return device


async def restore_demo_baselines(db: AsyncSession) -> dict:
    """恢复四位主展示老人的固定状态，并立即生成健康快照和评分。"""
    elders = list(
        (
            await db.execute(
                select(Elder)
                .where(Elder.name.in_(list(DEMO_BASELINES)))
                .order_by(Elder.id)
            )
        ).scalars().all()
    )
    restored = []
    for elder in elders:
        baseline = DEMO_BASELINES[elder.name]
        # 恢复基线时只清理近 24 小时的模拟器读数，避免上一轮异常档位继续
        # 拉低已经切回健康平稳老人的评分；真机/串口来源的数据原样保留。
        await db.execute(
            delete(HealthRecord).where(
                HealthRecord.elder_id == elder.id,
                HealthRecord.source == "simulator",
                HealthRecord.recorded_at >= datetime.now() - timedelta(hours=24),
            )
        )
        await _ensure_demo_device(db, elder, DeviceType.WATCH)
        await _ensure_demo_device(db, elder, DeviceType.DOG)
        await set_online(db, elder, DeviceType.WATCH, baseline["watch_online"])
        await set_online(db, elder, DeviceType.DOG, baseline["dog_online"])
        result = await apply_health_profile(
            db,
            elder,
            baseline["profile"],
            points=14,
            check_alarm=False,
        )
        restored.append(
            {
                "elder_id": elder.id,
                "elder_name": elder.name,
                "profile": result["profile"],
                "label": result["label"],
                "watch_online": baseline["watch_online"],
                "dog_online": baseline["dog_online"],
            }
        )
    await db.flush()
    logger.info("已恢复 %d 位老人的演示基线", len(restored))
    return {"ok": len(restored) == len(DEMO_BASELINES), "restored": restored}


async def keep_simulated_data_fresh(db: AsyncSession) -> int:
    """为已启用模拟档位的手表续一条数据，防止长时间展示后字段过期为空。"""
    watches = list(
        (
            await db.execute(
                select(Device).where(Device.device_type == DeviceType.WATCH)
            )
        ).scalars().all()
    )
    refreshed = 0
    cutoff = datetime.now() - timedelta(seconds=DEMO_FRESH_SECONDS)
    for watch in watches:
        profile = (watch.extra or {}).get(SIMULATOR_PROFILE_KEY)
        if not profile or not watch.elder_id:
            continue
        latest = await health_service.latest_record(db, watch.elder_id)
        if latest and latest.recorded_at >= cutoff:
            continue
        elder = await db.get(Elder, watch.elder_id)
        if not elder:
            continue
        await apply_health_profile(
            db, elder, profile, points=1, check_alarm=False
        )
        refreshed += 1
    return refreshed


async def trigger_fall(
    db: AsyncSession, elder: Elder, source: str = "dog", confidence: float = 0.9
) -> dict:
    device = await device_service.get_elder_device(
        db, elder.id, DeviceType.DOG if source == "dog" else DeviceType.WATCH
    )
    task = await outing_service.get_active_task(db, elder.id)
    lat, lng = elder.home_lat, elder.home_lng
    if device and device.last_lat and device.last_lng:
        lat, lng = device.last_lat, device.last_lng

    _event, alarm, is_new = await alarm_service.handle_fall_event(
        db,
        elder_id=elder.id,
        source=source,
        device_id=device.device_id if device else "",
        confidence=confidence,
        lat=lat,
        lng=lng,
        raw={"simulated": True, "task_id": task.id if task else None},
    )
    return {
        "alarm_id": alarm.id,
        "is_new_alarm": is_new,
        "status": alarm.status,
        "sources": (alarm.data or {}).get("sources", []),
    }


async def walk(
    db: AsyncSession, elder: Elder, steps: int = 5, meters_per_step: float = 60.0
) -> dict:
    """模拟老人带着机器狗向外走一段距离, 用于演示轨迹与电子围栏。"""
    task = await outing_service.get_active_task(db, elder.id)
    if not task:
        return {"ok": False, "message": "当前没有进行中的外出任务"}

    dog = await _dog_device(db, elder.id)
    lat = dog.last_lat if dog and dog.last_lat else task.origin_lat
    lng = dog.last_lng if dog and dog.last_lng else task.origin_lng
    bearing = random.uniform(0, 6.28)

    points = []
    for _ in range(steps):
        # 约 111320 米/纬度, 经度按纬度收敛
        lat += (meters_per_step * random.uniform(0.7, 1.0)) / 111320 * random.uniform(0.4, 1.0)
        lng += (meters_per_step * random.uniform(0.7, 1.0)) / 92000 * random.uniform(0.4, 1.0)
        point = await outing_service.record_track_point(
            db, task, lat, lng, speed=round(random.uniform(0.6, 1.2), 2), source="dog"
        )
        points.append({"lat": lat, "lng": lng, "distance_m": point.distance_from_origin_m})

    if dog:
        await device_service.touch(db, dog, lat=lat, lng=lng)
    return {
        "ok": True,
        "task_id": task.id,
        "points": points,
        "distance_from_origin_m": round(
            haversine_m(task.origin_lat, task.origin_lng, lat, lng), 1
        ),
        "geofence_radius_m": task.geofence_radius_m,
    }


async def reset_demo_state(db: AsyncSession, elder: Elder, clear_health: bool = False) -> dict:
    """清空该老人的演示痕迹, 让下一轮演示从干净状态开始。

    只清理外出、告警、跌倒事件与对话, 默认保留历史健康数据(趋势分析需要)。
    """
    from sqlalchemy import delete, select

    from app.models import (
        Alarm,
        AlarmNotification,
        Conversation,
        FallEvent,
        Message,
        OutingDecision,
        OutingRequest,
        OutingTask,
        TrackPoint,
    )

    task = await outing_service.get_active_task(db, elder.id)
    if task:
        await outing_service.end_task(
            db, task, end_reason="manual", status="aborted"
        )

    task_ids = [
        row[0]
        for row in (
            await db.execute(select(OutingTask.id).where(OutingTask.elder_id == elder.id))
        ).all()
    ]
    alarm_ids = [
        row[0]
        for row in (
            await db.execute(select(Alarm.id).where(Alarm.elder_id == elder.id))
        ).all()
    ]
    conversation_ids = [
        row[0]
        for row in (
            await db.execute(
                select(Conversation.id).where(Conversation.elder_id == elder.id)
            )
        ).all()
    ]

    if task_ids:
        await db.execute(delete(TrackPoint).where(TrackPoint.task_id.in_(task_ids)))
    if alarm_ids:
        await db.execute(
            delete(AlarmNotification).where(AlarmNotification.alarm_id.in_(alarm_ids))
        )
    if conversation_ids:
        await db.execute(
            delete(Message).where(Message.conversation_id.in_(conversation_ids))
        )

    await db.execute(delete(FallEvent).where(FallEvent.elder_id == elder.id))
    await db.execute(delete(Alarm).where(Alarm.elder_id == elder.id))
    await db.execute(delete(OutingTask).where(OutingTask.elder_id == elder.id))
    await db.execute(delete(OutingDecision).where(OutingDecision.elder_id == elder.id))
    await db.execute(delete(OutingRequest).where(OutingRequest.elder_id == elder.id))
    await db.execute(delete(Conversation).where(Conversation.elder_id == elder.id))

    if clear_health:
        await health_service.purge_elder_health(db, elder.id)

    from app.core.cache import cache

    await cache.delete(f"fall_confirm:{elder.id}")
    await cache.delete(f"dialog:{elder.id}")
    await db.flush()
    logger.info("已重置 %s 的演示数据(清空健康数据=%s)", elder.name, clear_health)
    return {
        "ok": True,
        "cleared": {
            "outing_tasks": len(task_ids),
            "alarms": len(alarm_ids),
            "conversations": len(conversation_ids),
            "health_records_cleared": clear_health,
        },
    }


async def state(db: AsyncSession, elder: Elder) -> dict:
    watch = await _watch_device(db, elder.id)
    dog = await _dog_device(db, elder.id)
    task = await outing_service.get_active_task(db, elder.id)
    latest = await health_service.latest_record(db, elder.id)
    return {
        "elder": {"id": elder.id, "name": elder.name, "city": elder.city},
        "watch": {
            "device_id": watch.device_id if watch else "",
            "status": watch.status if watch else "unbound",
            "battery": watch.battery if watch else 0,
        },
        "dog": {
            "device_id": dog.device_id if dog else "",
            "status": dog.status if dog else "unbound",
            "battery": dog.battery if dog else 0,
            "follow_mode": bool((dog.extra or {}).get("follow_mode")) if dog else False,
            "lat": dog.last_lat if dog else None,
            "lng": dog.last_lng if dog else None,
        },
        "active_task": (
            {
                "id": task.id,
                "started_at": task.started_at,
                "planned_duration_minutes": task.planned_duration_minutes,
                "geofence_radius_m": task.geofence_radius_m,
                "max_distance_m": round(task.max_distance_m, 1),
                "origin": {"lat": task.origin_lat, "lng": task.origin_lng},
            }
            if task
            else None
        ),
        "latest_health": (
            {
                "recorded_at": latest.recorded_at,
                "heart_rate": latest.heart_rate,
                "systolic": latest.systolic,
                "diastolic": latest.diastolic,
                "spo2": latest.spo2,
                "temperature": latest.temperature,
                "steps": latest.steps,
            }
            if latest
            else None
        ),
        "mqtt": mqtt_bridge.status(),
        "profiles": {
            key: value["label"] for key, value in HEALTH_PROFILES.items()
        },
    }
