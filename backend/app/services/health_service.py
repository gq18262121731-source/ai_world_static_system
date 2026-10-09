from datetime import date, datetime, time, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import hub
from app.core.formatting import format_vital
from app.core.logging import get_logger
from app.models import Elder, HealthDailySummary, HealthRecord
from app.rules.config_store import get_thresholds_for
from app.schemas.health import (
    HealthAssessment,
    HealthRecordIn,
    HealthTrendOut,
    HealthTrendPoint,
    LiveElderVitals,
    LiveSeriesPoint,
    VitalStat,
)
from app.services import data_quality

logger = get_logger(__name__)

VITAL_LABELS = {
    "heart_rate": ("心率", "次/分"),
    "spo2": ("血氧", "%"),
    "systolic": ("收缩压", "mmHg"),
    "diastolic": ("舒张压", "mmHg"),
    "temperature": ("体温", "℃"),
}

# 单项异常对综合风险分的贡献
RISK_WEIGHTS = {
    "danger": 30.0,
    "warning": 12.0,
}

# 判断"刚刚是否出现过危险值"的回溯窗口
RECENT_WINDOW_MINUTES = 60

# 单个指标最多沿用多久。真机手环分包上报，一条记录往往只带一部分字段，
# 必须按字段各取最近一次有效值，否则最新那条是血压包时心率就成了"无数据"。
FIELD_MAX_AGE_MINUTES = 10


def latest_field_values(
    records: list[HealthRecord], max_age_minutes: int = FIELD_MAX_AGE_MINUTES
) -> dict[str, tuple[float, datetime]]:
    """按字段各取最近一次非空值，返回 {字段: (值, 采集时刻)}。

    records 需按时间升序。超过 max_age_minutes 的值视为过期，不再沿用。
    """
    deadline = datetime.now() - timedelta(minutes=max_age_minutes)
    result: dict[str, tuple[float, datetime]] = {}
    for record in reversed(records):
        if record.recorded_at < deadline:
            break
        for key in (*VITAL_LABELS, "steps", "battery", "sleep_hours"):
            if key in result:
                continue
            value = record.trusted(key)
            if value is not None:
                result[key] = (float(value), record.recorded_at)
    return result


def trusted_values(records: list[HealthRecord], key: str) -> list[float]:
    """某字段的全部可信读数（跳过被标记为坏读数的）。"""
    return [float(r.trusted(key)) for r in records if r.trusted(key) is not None]


async def ingest(db: AsyncSession, elder_id: int, data: HealthRecordIn) -> HealthRecord:
    recorded_at = data.recorded_at or datetime.now()
    # 判定坏读数需要之前几分钟的读数做基线
    history = await list_records(
        db,
        elder_id,
        start=recorded_at - timedelta(minutes=data_quality.BASELINE_MINUTES),
        end=recorded_at,
        limit=600,
    )
    record = HealthRecord(
        elder_id=elder_id,
        device_id=data.device_id,
        recorded_at=recorded_at,
        heart_rate=data.heart_rate,
        systolic=data.systolic,
        diastolic=data.diastolic,
        spo2=data.spo2,
        temperature=data.temperature,
        steps=data.steps,
        sleep_hours=data.sleep_hours,
        activity_level=data.activity_level,
        battery=data.battery,
        lat=data.lat,
        lng=data.lng,
        source=data.source,
    )
    flagged = data_quality.apply(record, history)
    if flagged:
        logger.info(
            "老人 %s 的读数被判为孤立坏读数并跳过: %s",
            elder_id,
            ", ".join(f"{k}={getattr(record, k)}" for k in sorted(flagged)),
        )
    db.add(record)
    await db.flush()
    await hub.broadcast(
        "health_record",
        {
            "elder_id": elder_id,
            "recorded_at": record.recorded_at,
            "heart_rate": record.heart_rate,
            "spo2": record.spo2,
            "systolic": record.systolic,
            "diastolic": record.diastolic,
            "temperature": record.temperature,
            "steps": record.steps,
            "battery": record.battery,
            "suspicious_fields": sorted(flagged),
        },
        elder_id=elder_id,
    )
    return record


async def reflag_outliers(db: AsyncSession, elder_id: int, hours: int = 24) -> dict[str, int]:
    """对已入库的历史数据重新判定坏读数，返回各字段被标记的条数。"""
    records = await list_records(
        db, elder_id, start=datetime.now() - timedelta(hours=hours), limit=20000
    )
    counts = data_quality.reflag(records)
    await db.flush()
    return counts


async def list_records(
    db: AsyncSession,
    elder_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 500,
) -> list[HealthRecord]:
    stmt = select(HealthRecord).where(HealthRecord.elder_id == elder_id)
    if start:
        stmt = stmt.where(HealthRecord.recorded_at >= start)
    if end:
        stmt = stmt.where(HealthRecord.recorded_at <= end)
    stmt = stmt.order_by(HealthRecord.recorded_at.desc()).limit(limit)
    rows = list((await db.execute(stmt)).scalars().all())
    rows.reverse()
    return rows


async def latest_record(db: AsyncSession, elder_id: int) -> HealthRecord | None:
    result = await db.execute(
        select(HealthRecord)
        .where(HealthRecord.elder_id == elder_id)
        .order_by(HealthRecord.recorded_at.desc())
        .limit(1)
    )
    return result.scalars().first()


TREND_MAX_HOURS = 24 * 7
TREND_MAX_POINTS = 720


def bucket_trend(
    records: list[HealthRecord], key: str, start: datetime, end: datetime, points: int
) -> tuple[list[HealthTrendPoint], int, int]:
    """把窗口内的可信读数按等宽时间桶汇总成 avg/min/max。

    records 需按时间升序。返回 (桶列表, 可信读数条数, 被剔除的疑似坏读数条数)。
    桶宽不小于 5 秒（采样间隔），点数少时桶里只有一条读数，曲线就是原始曲线。
    """
    span = max(1.0, (end - start).total_seconds())
    bucket_seconds = max(5, int(span / max(1, points)))
    buckets: dict[int, list[tuple[datetime, float]]] = {}
    total = dropped = 0
    for record in records:
        raw = getattr(record, key, None)
        if raw is None:
            continue
        value = record.trusted(key)
        if value is None:
            dropped += 1
            continue
        total += 1
        index = int((record.recorded_at - start).total_seconds() // bucket_seconds)
        buckets.setdefault(index, []).append((record.recorded_at, float(value)))
    result = []
    for index, samples in sorted(buckets.items()):
        values = [value for _, value in samples]
        lowest = min(samples, key=lambda item: item[1])
        highest = max(samples, key=lambda item: item[1])
        result.append(
            HealthTrendPoint(
                t=start + timedelta(seconds=(index + 0.5) * bucket_seconds),
                avg=round(sum(values) / len(values), 2),
                min=lowest[1],
                max=highest[1],
                n=len(values),
                t_min=lowest[0],
                t_max=highest[0],
            )
        )
    return result, total, dropped


async def build_trend(
    db: AsyncSession, elder_id: int, metric: str, hours: int = 24, points: int = 240
) -> HealthTrendOut:
    """单项体征的折线数据：窗口内分桶，剔除疑似坏读数。"""
    if metric not in VITAL_LABELS:
        raise ValueError(f"不支持的指标: {metric}")
    hours = max(1, min(hours, TREND_MAX_HOURS))
    points = max(10, min(points, TREND_MAX_POINTS))
    end = datetime.now()
    start = end - timedelta(hours=hours)
    stmt = (
        select(HealthRecord)
        .where(
            HealthRecord.elder_id == elder_id,
            HealthRecord.recorded_at >= start,
            getattr(HealthRecord, metric).is_not(None),
        )
        .order_by(HealthRecord.recorded_at.asc())
    )
    records = list((await db.execute(stmt)).scalars().all())
    series, total, dropped = bucket_trend(records, metric, start, end, points)
    label, unit = VITAL_LABELS[metric]
    latest = next((r for r in reversed(records) if r.trusted(metric) is not None), None)
    return HealthTrendOut(
        metric=metric,
        label=label,
        unit=unit,
        hours=hours,
        bucket_seconds=max(5, int((end - start).total_seconds() / points)),
        points=series,
        total=total,
        dropped=dropped,
        latest=float(latest.trusted(metric)) if latest else None,
        latest_at=latest.recorded_at if latest else None,
    )


def _classify(key: str, value: float | None, rules: dict) -> str:
    if value is None:
        return "unknown"
    if key == "heart_rate":
        cfg = rules["heart_rate"]
        if value < cfg["block_min"] or value > cfg["block_max"]:
            return "danger"
        if value < cfg["warn_min"] or value > cfg["warn_max"]:
            return "warning"
    elif key == "spo2":
        cfg = rules["spo2"]
        if value < cfg["block_min"]:
            return "danger"
        if value < cfg["warn_min"]:
            return "warning"
    elif key == "systolic":
        cfg = rules["blood_pressure"]
        if value > cfg["systolic_block_max"] or value < cfg["systolic_block_min"]:
            return "danger"
        if value > cfg["systolic_warn_max"] or value < cfg["systolic_warn_min"]:
            return "warning"
    elif key == "diastolic":
        cfg = rules["blood_pressure"]
        if value > cfg["diastolic_block_max"]:
            return "danger"
        if value > cfg["diastolic_warn_max"]:
            return "warning"
    elif key == "temperature":
        cfg = rules["temperature"]
        if value > cfg["block_max"] or value < cfg["block_min"]:
            return "danger"
        if value > cfg["warn_max"]:
            return "warning"
    return "normal"


def _stat(values: list[float]) -> tuple[float | None, float | None, float | None]:
    if not values:
        return None, None, None
    return (
        round(sum(values) / len(values), 1),
        min(values),
        max(values),
    )


async def build_assessment(
    db: AsyncSession,
    elder: Elder,
    window_hours: int = 24,
    with_score: bool = True,
) -> HealthAssessment:
    """生成外出评估与语音问答共用的健康快照。

    with_score 为真时顺带算出健康评分并写入当日日摘要——评估在每次上报后都会跑，
    所以日摘要里的分数始终是最新的，首页不必再算一遍。
    """
    rules = await get_thresholds_for(db, elder)
    now = datetime.now()
    # 真机手环每 5 秒一条，一天有 1.7 万条；上限太小的话"24 小时区间"其实只覆盖了最近几个小时
    records = await list_records(
        db, elder.id, start=now - timedelta(hours=window_hours), limit=max(2000, window_hours * 1000)
    )
    assessment = HealthAssessment(elder_id=elder.id, window_hours=window_hours)

    if not records:
        assessment.has_data = False
        assessment.summary_text = "近期没有采集到手表健康数据"
        return assessment

    assessment.has_data = True
    latest = records[-1]
    assessment.latest_at = latest.recorded_at
    # 真机分包上报时，各指标要各取各的最近有效值
    field_values = latest_field_values(records)
    assessment.watch_battery = (
        int(field_values["battery"][0]) if "battery" in field_values else None
    )

    # 当前状态以最新值为准; 最近一小时内出现过危险值只做提醒, 不直接判危
    recent_cutoff = now - timedelta(minutes=RECENT_WINDOW_MINUTES)
    recent_records = [r for r in records if r.recorded_at >= recent_cutoff]

    for key, (label, unit) in VITAL_LABELS.items():
        series = trusted_values(records, key)
        latest_value = field_values[key][0] if key in field_values else None
        avg, vmin, vmax = _stat(series)
        status = _classify(key, latest_value, rules)
        note = ""
        recent_extreme = None

        if status == "normal":
            # 孤立坏读数已被 trusted() 过滤，否则一条 214 的心率会让这里提醒一小时
            recent_extremes = [
                value
                for value in trusted_values(recent_records, key)
                if _classify(key, value, rules) == "danger"
            ]
            if recent_extremes:
                status = "warning"
                recent_extreme = min(recent_extremes) if key == "spo2" else max(recent_extremes)
                note = (
                    f"{label}当前 {format_vital(key, latest_value)}{unit} 已恢复正常，"
                    f"但最近 {RECENT_WINDOW_MINUTES} 分钟内曾出现 "
                    f"{format_vital(key, recent_extreme)}{unit}"
                )

        assessment.vitals[key] = VitalStat(
            latest=float(latest_value) if latest_value is not None else None,
            avg=avg,
            min=vmin,
            max=vmax,
            unit=unit,
            status=status,
            label=label,
            note=note,
            recent_extreme=recent_extreme,
        )
        if status in ("warning", "danger") and latest_value is not None:
            if note:
                assessment.abnormal_items.append(note)
            else:
                level = "偏离正常范围" if status == "warning" else "明显异常"
                assessment.abnormal_items.append(
                    f"{label} {format_vital(key, latest_value)}{unit} {level}"
                )

    # 睡眠时长是“昨夜”的最新结论，不应取 24 小时内所有上报中的最大值。
    # 否则先演示过健康档位后再切到“疲劳缺觉”，旧的 8 小时读数会一直盖住新状态。
    assessment.sleep_hours_last_night = (
        round(field_values["sleep_hours"][0], 1)
        if "sleep_hours" in field_values
        else None
    )

    today_start = datetime.combine(date.today(), time.min)
    steps_today = await db.execute(
        select(func.max(HealthRecord.steps)).where(
            HealthRecord.elder_id == elder.id,
            HealthRecord.recorded_at >= today_start,
        )
    )
    assessment.steps_today = steps_today.scalar()

    assessment.risk_score = round(
        min(
            100.0,
            sum(
                RISK_WEIGHTS.get(v.status, 0.0)
                for v in assessment.vitals.values()
            ),
        ),
        1,
    )
    assessment.trend_notes.extend(await _trend_notes(db, elder.id))
    assessment.summary_text = _compose_summary(assessment)

    if with_score:
        from app.services import health_score  # 局部导入避免循环依赖

        inputs, classify = await health_score.gather_inputs(db, elder, assessment, records, rules)
        assessment.health_score = health_score.compute_score(inputs, classify)
        # 只有 24 小时口径的分数才是"当日健康评分"。告警评估用的 1 小时窗口
        # 看不到昨夜睡眠这类数据，算出来的分数不能覆盖日摘要里的
        if window_hours >= 24:
            await health_score.persist_daily_score(db, elder.id, assessment.health_score)
    return assessment


async def live_snapshot(
    db: AsyncSession,
    elder: Elder,
    points: int = 30,
    stale_after_seconds: int = 300,
) -> LiveElderVitals:
    """首页实时卡片用的轻量快照: 只取最近若干个采样点, 不做趋势与日摘要计算。"""
    rules = await get_thresholds_for(db, elder)
    result = await db.execute(
        select(HealthRecord)
        .where(HealthRecord.elder_id == elder.id)
        .order_by(HealthRecord.recorded_at.desc())
        .limit(points)
    )
    records = list(result.scalars().all())
    records.reverse()

    snapshot = LiveElderVitals(elder_id=elder.id, elder_name=elder.name)
    if not records:
        return snapshot

    latest = records[-1]
    snapshot.latest_at = latest.recorded_at
    snapshot.seconds_since_update = max(
        0, int((datetime.now() - latest.recorded_at).total_seconds())
    )
    snapshot.is_stale = snapshot.seconds_since_update > stale_after_seconds
    field_values = latest_field_values(records)
    snapshot.watch_battery = (
        int(field_values["battery"][0]) if "battery" in field_values else None
    )

    severity = {"unknown": 0, "normal": 1, "warning": 2, "danger": 3}
    worst = "unknown"
    for key, (label, unit) in VITAL_LABELS.items():
        values = trusted_values(records, key)
        latest_value = field_values[key][0] if key in field_values else None
        status = _classify(key, latest_value, rules)
        avg, vmin, vmax = _stat(values)
        snapshot.vitals[key] = VitalStat(
            latest=float(latest_value) if latest_value is not None else None,
            avg=avg,
            min=vmin,
            max=vmax,
            unit=unit,
            status=status,
            label=label,
        )
        if status in ("warning", "danger") and latest_value is not None:
            level = "偏离正常范围" if status == "warning" else "明显异常"
            snapshot.abnormal_items.append(
                f"{label} {format_vital(key, latest_value)}{unit} {level}"
            )
        if severity[status] > severity[worst]:
            worst = status
    snapshot.risk_level = worst

    # 卡片直接展示业务状态，避免观众需要从一串数值自行推断。
    statuses: list[str] = []
    sleep_hours = field_values.get("sleep_hours", (None, None))[0]
    if sleep_hours is not None and sleep_hours < rules["sleep"]["warn_min_hours"]:
        statuses.append("疲劳缺觉")
    temperature = snapshot.vitals.get("temperature")
    if (
        temperature
        and temperature.latest is not None
        and temperature.latest > rules["temperature"]["warn_max"]
    ):
        statuses.append("发热")
    spo2 = snapshot.vitals.get("spo2")
    if (
        spo2
        and spo2.latest is not None
        and spo2.latest < rules["spo2"]["warn_min"]
    ):
        statuses.append("血氧偏低")
    systolic = snapshot.vitals.get("systolic")
    diastolic = snapshot.vitals.get("diastolic")
    if (
        (systolic and systolic.latest is not None and systolic.latest > rules["blood_pressure"]["systolic_warn_max"])
        or (diastolic and diastolic.latest is not None and diastolic.latest > rules["blood_pressure"]["diastolic_warn_max"])
    ):
        statuses.append("血压偏高")
    heart_rate = snapshot.vitals.get("heart_rate")
    if (
        heart_rate
        and heart_rate.latest is not None
        and heart_rate.latest > rules["heart_rate"]["warn_max"]
    ):
        statuses.append("心率过快")
    snapshot.body_statuses = statuses or (["健康平稳"] if worst == "normal" else [])

    today_start = datetime.combine(date.today(), time.min)
    steps = await db.execute(
        select(func.max(HealthRecord.steps)).where(
            HealthRecord.elder_id == elder.id, HealthRecord.recorded_at >= today_start
        )
    )
    snapshot.steps_today = steps.scalar()

    # 健康评分直接读日摘要，每次上报后的评估都会刷新它，这里不必重算
    scored = await db.execute(
        select(HealthDailySummary)
        .where(
            HealthDailySummary.elder_id == elder.id,
            HealthDailySummary.summary_date >= date.today() - timedelta(days=1),
        )
        .order_by(HealthDailySummary.summary_date)
    )
    by_date = {row.summary_date: row for row in scored.scalars().all()}
    today = by_date.get(date.today())
    yesterday = by_date.get(date.today() - timedelta(days=1))
    if today and today.health_score is not None:
        snapshot.health_score = int(round(today.health_score))
        snapshot.health_grade = today.health_grade or ""
        snapshot.score_reference = (today.health_confidence or 1.0) < 0.6
        if yesterday and yesterday.health_score is not None:
            snapshot.score_delta = int(round(today.health_score - yesterday.health_score))

    # 曲线里也跳过坏读数，不然首页的小折线会被一个尖峰压扁
    snapshot.series = [
        LiveSeriesPoint(
            ts=r.recorded_at,
            heart_rate=r.trusted("heart_rate"),
            spo2=r.trusted("spo2"),
            systolic=r.trusted("systolic"),
        )
        for r in records
    ]
    return snapshot


async def _trend_notes(db: AsyncSession, elder_id: int, days: int = 3) -> list[str]:
    """对比近 days 天与之前的日摘要, 给出趋势描述。"""
    summaries = await list_summaries(db, elder_id, days=days + 4)
    if len(summaries) < 2:
        return []
    recent = summaries[-days:]
    earlier = summaries[:-days] or summaries[:1]
    notes: list[str] = []

    def _avg(items: list, attr: str) -> float | None:
        values = [getattr(i, attr) for i in items if getattr(i, attr) is not None]
        return sum(values) / len(values) if values else None

    pairs = [
        ("hr_avg", "平均心率", 5, "次/分"),
        ("spo2_avg", "平均血氧", 1.5, "%"),
        ("systolic_avg", "平均收缩压", 8, "mmHg"),
        ("sleep_hours", "睡眠时长", 1.0, "小时"),
    ]
    for attr, label, threshold, unit in pairs:
        new = _avg(recent, attr)
        old = _avg(earlier, attr)
        if new is None or old is None:
            continue
        delta = new - old
        if abs(delta) >= threshold:
            direction = "上升" if delta > 0 else "下降"
            notes.append(
                f"近{days}日{label}较前期{direction} {abs(round(delta, 1))}{unit}"
            )
    risk_recent = _avg(recent, "risk_score")
    if risk_recent and risk_recent >= 30:
        notes.append(f"近{days}日综合风险评分偏高({round(risk_recent, 1)})")
    return notes


def _compose_summary(assessment: HealthAssessment) -> str:
    parts: list[str] = []
    for key in ("heart_rate", "spo2", "systolic", "temperature"):
        stat = assessment.vitals.get(key)
        if stat and stat.latest is not None:
            if key == "systolic":
                dia = assessment.vitals.get("diastolic")
                dia_text = (
                    f"/{format_vital('diastolic', dia.latest)}"
                    if dia and dia.latest is not None
                    else ""
                )
                parts.append(
                    f"血压 {format_vital(key, stat.latest)}{dia_text} mmHg"
                )
            else:
                parts.append(
                    f"{stat.label} {format_vital(key, stat.latest)}{stat.unit}"
                )
    if assessment.sleep_hours_last_night:
        parts.append(f"昨夜睡眠 {assessment.sleep_hours_last_night} 小时")
    if assessment.steps_today is not None:
        parts.append(f"今日步数 {assessment.steps_today}")
    text = "，".join(parts)
    if assessment.abnormal_items:
        text += "；异常项：" + "、".join(assessment.abnormal_items)
    return text


async def rebuild_daily_summary(
    db: AsyncSession, elder: Elder, target: date | None = None
) -> HealthDailySummary | None:
    """重算某天的健康日摘要。"""
    target = target or date.today()
    start = datetime.combine(target, time.min)
    end = datetime.combine(target, time.max)
    records = await list_records(db, elder.id, start=start, end=end, limit=5000)
    if not records:
        return None
    rules = await get_thresholds_for(db, elder)

    def _values(attr: str) -> list[float]:
        return trusted_values(records, attr)

    hr = _values("heart_rate")
    spo2 = _values("spo2")
    systolic = _values("systolic")
    diastolic = _values("diastolic")
    temperature = _values("temperature")
    steps = _values("steps")
    sleep = _values("sleep_hours")

    anomaly = 0
    for attr, values in (
        ("heart_rate", hr),
        ("spo2", spo2),
        ("systolic", systolic),
        ("diastolic", diastolic),
        ("temperature", temperature),
    ):
        anomaly += sum(1 for v in values if _classify(attr, v, rules) != "normal")

    total_points = sum(
        len(v) for v in (hr, spo2, systolic, diastolic, temperature)
    ) or 1
    risk = round(min(100.0, anomaly / total_points * 100 * 1.5), 1)

    existing = await db.execute(
        select(HealthDailySummary).where(
            HealthDailySummary.elder_id == elder.id,
            HealthDailySummary.summary_date == target,
        )
    )
    summary = existing.scalars().first() or HealthDailySummary(
        elder_id=elder.id, summary_date=target
    )
    summary.hr_avg = round(sum(hr) / len(hr), 1) if hr else None
    summary.hr_min = int(min(hr)) if hr else None
    summary.hr_max = int(max(hr)) if hr else None
    summary.spo2_avg = round(sum(spo2) / len(spo2), 1) if spo2 else None
    summary.spo2_min = int(min(spo2)) if spo2 else None
    summary.systolic_avg = (
        round(sum(systolic) / len(systolic), 1) if systolic else None
    )
    summary.diastolic_avg = (
        round(sum(diastolic) / len(diastolic), 1) if diastolic else None
    )
    summary.temperature_avg = (
        round(sum(temperature) / len(temperature), 2) if temperature else None
    )
    summary.steps_total = int(max(steps)) if steps else None
    summary.sleep_hours = round(sleep[-1], 1) if sleep else None
    summary.anomaly_count = anomaly
    summary.risk_score = risk
    # 个人基线用的两个每日统计量
    from app.services.health_score import resting_heart_rate

    summary.resting_hr = resting_heart_rate(hr)
    summary.spo2_median = round(sorted(spo2)[len(spo2) // 2], 1) if spo2 else None
    summary.detail = {
        "sample_count": len(records),
        "suspicious_count": sum(1 for r in records if r.suspicious_fields),
    }
    db.add(summary)
    await db.flush()
    return summary


async def list_summaries(
    db: AsyncSession, elder_id: int, days: int = 7
) -> list[HealthDailySummary]:
    since = date.today() - timedelta(days=days - 1)
    result = await db.execute(
        select(HealthDailySummary)
        .where(
            HealthDailySummary.elder_id == elder_id,
            HealthDailySummary.summary_date >= since,
        )
        .order_by(HealthDailySummary.summary_date)
    )
    return list(result.scalars().all())


async def purge_elder_health(db: AsyncSession, elder_id: int) -> None:
    await db.execute(delete(HealthRecord).where(HealthRecord.elder_id == elder_id))
    await db.execute(
        delete(HealthDailySummary).where(HealthDailySummary.elder_id == elder_id)
    )
