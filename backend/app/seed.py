"""初始化演示数据: python -m app.seed"""

import asyncio
import random
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select

from app.core.db import SessionLocal, engine
from app.core.logging import get_logger, setup_logging
from app.core.security import hash_password
from app.models import (
    Base,
    Device,
    Elder,
    EmergencyContact,
    FamilyLink,
    RuleConfig,
    User,
)
from app.models.enums import DeviceStatus, DeviceType, UserRole
from app.services import health_service
from app.services.simulator_service import generate_vitals

logger = get_logger(__name__)

USERS = [
    ("admin", "admin123", "系统管理员", UserRole.ADMIN, "13800000001"),
    ("staff", "staff123", "社区网格员 王五", UserRole.STAFF, "13800000002"),
    ("family", "family123", "张三的儿子 赵六", UserRole.FAMILY, "13900000001"),
]

ELDERS = [
    {
        "name": "张三",
        "gender": "男",
        "birth_date": date(1948, 3, 12),
        "phone": "13700000001",
        "address": "",
        "city": "杭州",
        "home_lat": 30.2795,
        "home_lng": 120.1330,
        "chronic_diseases": ["高血压", "2型糖尿病"],
        "mobility_level": "good",
        "note": "独居，喜欢每天早上去附近公园散步。",
        "contacts": [
            ("赵六", "13900000001", "儿子", 1),
            ("孙七", "13900000002", "女儿", 2),
        ],
        "devices": [
            ("WATCH-LJG-001", DeviceType.WATCH, "张三的健康手表"),
            ("DOG-LJG-001", DeviceType.DOG, "陪护机器狗小安"),
        ],
    },
    {
        "name": "李四",
        "gender": "女",
        "birth_date": date(1943, 9, 5),
        "phone": "13700000002",
        "address": "",
        "city": "杭州",
        "home_lat": 30.3120,
        "home_lng": 120.1420,
        "chronic_diseases": ["冠心病", "慢阻肺"],
        "mobility_level": "limited",
        "note": "行动稍有不便，外出需缩短时长与半径。",
        "contacts": [("周八", "13900000003", "儿子", 1)],
        "devices": [
            ("WATCH-ZXL-001", DeviceType.WATCH, "李四的健康手表"),
            ("DOG-ZXL-001", DeviceType.DOG, "陪护机器狗小暖"),
        ],
    },
    {
        "name": "王五",
        "gender": "男",
        "birth_date": date(1947, 7, 20),
        "phone": "13700000003",
        "address": "",
        "city": "杭州",
        "home_lat": 30.2884,
        "home_lng": 120.1612,
        "chronic_diseases": ["高血压"],
        "mobility_level": "good",
        "note": "与老伴同住，日常喜欢在小区内散步。",
        "contacts": [("王强", "13900000004", "儿子", 1)],
        "devices": [
            ("WATCH-WW-001", DeviceType.WATCH, "王五的健康手表"),
            ("DOG-WW-001", DeviceType.DOG, "陪护机器狗小康"),
        ],
    },
    {
        "name": "陈二",
        "gender": "女",
        "birth_date": date(1950, 11, 18),
        "phone": "13700000004",
        "address": "",
        "city": "杭州",
        "home_lat": 30.3012,
        "home_lng": 120.1498,
        "chronic_diseases": ["骨质疏松"],
        "mobility_level": "good",
        "note": "与女儿同住，晨间活动规律。",
        "contacts": [("陈敏", "13900000005", "女儿", 1)],
        "devices": [
            ("WATCH-CGF-001", DeviceType.WATCH, "陈二的健康手表"),
            ("DOG-CGF-001", DeviceType.DOG, "陪护机器狗小桂"),
        ],
    },
    {
        "name": "刘建国",
        "gender": "男",
        "birth_date": date(1945, 5, 26),
        "phone": "13700000005",
        "address": "",
        "city": "杭州",
        "home_lat": 30.2679,
        "home_lng": 120.1731,
        "chronic_diseases": ["2型糖尿病", "高血脂"],
        "mobility_level": "limited",
        "note": "独居，外出时建议携带手杖并缩短活动半径。",
        "contacts": [("刘晨", "13900000006", "儿子", 1)],
        "devices": [
            ("WATCH-LJG-002", DeviceType.WATCH, "刘建国的健康手表"),
            ("DOG-LJG-002", DeviceType.DOG, "陪护机器狗小国"),
        ],
    },
]

GLOBAL_RULES = [
    (
        "outing_frequency",
        {"max_per_day": 3, "warn_per_day": 2, "max_per_week": 14, "min_interval_minutes": 45},
        "外出次数与最短间隔",
    ),
    (
        "time_window",
        {"start": "06:00", "end": "18:30", "warn_before": "07:00", "warn_after": "17:30"},
        "允许外出的时段",
    ),
]


async def _seed_users(db) -> dict[str, User]:
    created: dict[str, User] = {}
    for username, password, real_name, role, phone in USERS:
        existing = (
            await db.execute(select(User).where(User.username == username))
        ).scalars().first()
        if existing:
            created[username] = existing
            continue
        user = User(
            username=username,
            password_hash=hash_password(password),
            real_name=real_name,
            role=role,
            phone=phone,
        )
        db.add(user)
        await db.flush()
        created[username] = user
        logger.info("创建用户 %s / %s (%s)", username, password, role)
    return created


async def _seed_elders(db) -> list[Elder]:
    elders: list[Elder] = []
    for item in ELDERS:
        existing = (
            await db.execute(select(Elder).where(Elder.name == item["name"]))
        ).scalars().first()
        if existing:
            elders.append(existing)
            continue
        elder = Elder(
            name=item["name"],
            gender=item["gender"],
            birth_date=item["birth_date"],
            phone=item["phone"],
            address=item["address"],
            city=item["city"],
            home_lat=item["home_lat"],
            home_lng=item["home_lng"],
            chronic_diseases=item["chronic_diseases"],
            mobility_level=item["mobility_level"],
            live_alone=True,
            note=item["note"],
        )
        db.add(elder)
        await db.flush()
        for name, phone, relation, priority in item["contacts"]:
            db.add(
                EmergencyContact(
                    elder_id=elder.id,
                    name=name,
                    phone=phone,
                    relation=relation,
                    priority=priority,
                )
            )
        for device_id, device_type, device_name in item["devices"]:
            db.add(
                Device(
                    device_id=device_id,
                    device_type=device_type,
                    name=device_name,
                    elder_id=elder.id,
                    status=DeviceStatus.ONLINE,
                    battery=random.randint(70, 95),
                    last_seen_at=datetime.now(),
                    last_lat=item["home_lat"],
                    last_lng=item["home_lng"],
                    extra={"follow_mode": False} if device_type == DeviceType.DOG else {},
                )
            )
        elders.append(elder)
        logger.info("创建老人档案 %s", elder.name)
    return elders


async def _seed_history(db, elder: Elder, days: int = 7) -> None:
    from app.models import HealthRecord

    count = (
        await db.execute(
            select(func.count(HealthRecord.id)).where(HealthRecord.elder_id == elder.id)
        )
    ).scalar() or 0
    if count > 0:
        return

    watch_id = f"WATCH-{elder.name}"
    device = (
        await db.execute(
            select(Device).where(
                Device.elder_id == elder.id, Device.device_type == DeviceType.WATCH
            )
        )
    ).scalars().first()
    if device:
        watch_id = device.device_id

    from app.schemas.health import HealthRecordIn

    now = datetime.now()
    for day_offset in range(days - 1, -1, -1):
        day = date.today() - timedelta(days=day_offset)
        steps = 0
        # 每天 06:00-21:00 每 30 分钟一条
        for slot in range(30):
            moment = datetime.combine(day, time(6, 0)) + timedelta(minutes=30 * slot)
            if moment > now:
                break
            profile = "normal" if random.random() > 0.12 else "tired"
            vitals = generate_vitals(profile)
            steps += random.randint(60, 320)
            await health_service.ingest(
                db,
                elder.id,
                HealthRecordIn(
                    device_id=watch_id,
                    recorded_at=moment,
                    steps=steps,
                    activity_level=random.choice(["rest", "light", "moderate"]),
                    battery=random.randint(60, 100),
                    lat=elder.home_lat,
                    lng=elder.home_lng,
                    source="watch",
                    **vitals,
                ),
            )
        await health_service.rebuild_daily_summary(db, elder, day)
    logger.info("为 %s 生成 %d 天历史健康数据", elder.name, days)


async def _seed_rules(db) -> None:
    for rule_key, value, description in GLOBAL_RULES:
        existing = (
            await db.execute(
                select(RuleConfig).where(
                    RuleConfig.rule_key == rule_key, RuleConfig.elder_id.is_(None)
                )
            )
        ).scalars().first()
        if existing:
            continue
        db.add(
            RuleConfig(
                elder_id=None, rule_key=rule_key, value=value, description=description
            )
        )


async def _link_family(db, users: dict[str, User], elders: list[Elder]) -> None:
    family = users.get("family")
    if not family or not elders:
        return
    existing = (
        await db.execute(select(FamilyLink).where(FamilyLink.user_id == family.id))
    ).scalars().first()
    if existing:
        return
    db.add(
        FamilyLink(
            user_id=family.id, elder_id=elders[0].id, relation="儿子", is_primary=True
        )
    )


async def seed() -> None:
    setup_logging()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as db:
        users = await _seed_users(db)
        elders = await _seed_elders(db)
        await _link_family(db, users, elders)
        await _seed_rules(db)
        await db.commit()

        for elder in elders:
            await _seed_history(db, elder)
            await db.commit()

    await engine.dispose()
    logger.info("演示数据初始化完成")
    logger.info("管理员 admin/admin123 | 网格员 staff/staff123 | 家属 family/family123")


if __name__ == "__main__":
    asyncio.run(seed())
