"""老人健康评分：0~100 的正向分数，越高越好，带等级、置信度与可解释的扣分原因。

七个维度，每一档阈值都有出处，不再拍脑袋：

- 急性体征风险：心率、血氧、收缩压、体温按英国皇家内科医师学会 NEWS2（2017）分档记 0~3 分。
  取"此刻"与"今日峰值按 3 小时半衰期衰减"的较大者，早上发过烧到中午仍算一半。
- 慢病控制达标：血压目标按年龄分（中国老年高血压管理指南：65~79 岁 <140/90，≥80 岁 <150/90），
  静息心率 50~90，血氧达到个人目标（慢阻肺用 NEWS2 Scale 2 的 88~92）。
- 个人基线偏离：今日静息心率与近 14 天自己的基线比较（Oura / WHOOP / Garmin 准备度模型的核心思路）。
- 稳定性：24 小时内异常读数占比与心率变异系数。
- 活动量：步数按 Paluch 2022（Lancet Public Health，47,471 人）中 ≥60 岁的剂量-反应曲线给分，
  6000~8000 步为平台期；行动不便者目标按比例折算。近 7 天外出次数占三成。
- 睡眠：美国国家睡眠基金会 65 岁以上建议 7~8 小时。
- 趋势：近 3 天与前期的日摘要比较，用中位数抗离群。

硬上限跟随 NEWS2 的临床响应阈值：任一单项 3 分总分不超过 69，合计 5~6 分不超过 54，合计 ≥7 分不超过 39。
缺项不扣分、权重归一；覆盖度与数据新鲜度合成置信度，置信度低的分数在界面上标"参考"。
步速（第六生命体征）留作后续维度，等机器狗真机跟随数据接入再加。
"""

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import median, pstdev

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.formatting import format_vital
from app.models import Elder, HealthDailySummary, HealthRecord, OutingTask
from app.models.enums import OutingTaskStatus
from app.rules.config_store import get_thresholds_for
from app.schemas.health import HealthAssessment, HealthScoreDimension, HealthScoreOut

WEIGHTS = {
    "acute": 28,
    "control": 17,
    "baseline": 11,
    "stability": 11,
    "activity": 17,
    "sleep": 11,
    "trend": 5,
}
LABELS = {
    "acute": "急性体征风险",
    "control": "慢病控制",
    "baseline": "个人基线",
    "stability": "稳定性",
    "activity": "活动量",
    "sleep": "睡眠",
    "trend": "趋势",
}
GRADES = ((85, "优"), (70, "良"), (55, "中"), (0, "差"))

VITAL_LABELS = {"heart_rate": "心率", "spo2": "血氧", "systolic": "收缩压", "diastolic": "舒张压", "temperature": "体温"}
VITAL_UNITS = {"heart_rate": "次/分", "spo2": "%", "systolic": "mmHg", "diastolic": "mmHg", "temperature": "℃"}

# ---------- 急性体征：NEWS2 ----------

# 峰值影响的半衰期：早上 9 点 3 分的发烧，到 12 点还算 1.5 分，到 18 点只剩 0.4 分
ACUTE_HALF_LIFE_HOURS = 3.0
# NEWS2 合计 -> 维度分；4 个参数最高 12 分
NEWS_TO_SCORE = ((0, 100), (1, 90), (2, 80), (3, 65), (4, 55), (5, 40), (6, 30), (7, 15), (8, 5), (12, 0))
# NEWS2 临床响应阈值对应的总分上限
CAP_SINGLE_PARAM_3 = 69
CAP_TOTAL_5 = 54
CAP_TOTAL_7 = 39
# 慢阻肺按 NEWS2 Scale 2（高碳酸血症型呼吸衰竭，血氧目标 88~92）计血氧
SCALE2_DISEASES = ("慢阻肺",)


def news_points(key: str, value: float, scale2: bool = False) -> int:
    """NEWS2 单项分档。不在表里的字段（舒张压）记 0。"""
    if key == "heart_rate":
        if value <= 40 or value >= 131:
            return 3
        if value >= 111:
            return 2
        if value >= 91 or value <= 50:
            return 1
        return 0
    if key == "spo2":
        if scale2:
            if value <= 83:
                return 3
            if value <= 85:
                return 2
            if value <= 87:
                return 1
            return 0
        if value <= 91:
            return 3
        if value <= 93:
            return 2
        if value <= 95:
            return 1
        return 0
    if key == "systolic":
        if value <= 90 or value >= 220:
            return 3
        if value <= 100:
            return 2
        if value <= 110:
            return 1
        return 0
    if key == "temperature":
        if value <= 35.0:
            return 3
        if value >= 39.1:
            return 2
        if value <= 36.0 or value >= 38.1:
            return 1
        return 0
    return 0


def _interp(table, x: float) -> float:
    """分段线性插值，x 超出两端取端点值。"""
    if x <= table[0][0]:
        return float(table[0][1])
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return float(table[-1][1])


# ---------- 慢病控制 ----------

# 收缩压目标按年龄（中国老年高血压管理指南）；舒张压目标统一 90
def systolic_target(age: int | None) -> int:
    return 150 if age is not None and age >= 80 else 140


RESTING_HR_RANGE = (50, 90)
SPO2_TARGET = 95
SPO2_TARGET_SCALE2 = 88

# ---------- 个人基线 ----------

BASELINE_DAYS = 14
BASELINE_MIN_DAYS = 5
# 静息心率高出基线：5 以内不扣，10 扣一半，15 扣完
RHR_DEVIATION = ((5, 100), (10, 50), (15, 0))

# ---------- 活动 ----------

# ≥60 岁步数剂量-反应曲线（Paluch 2022：风险随步数下降，6000~8000 步进入平台期）
STEP_CURVE = ((0, 0), (1000, 20), (2000, 40), (3000, 60), (4000, 75), (5000, 87), (6000, 95), (7000, 100))
# 行动不便的老人按比例折算：走 3600 步等价于行动良好者的 6000 步
MOBILITY_FACTOR = {"good": 1.0, "limited": 0.6, "poor": 0.35}
OUTING_GOAL_PER_WEEK = 3

# ---------- 睡眠（NSF 65 岁以上：建议 7~8 小时，5~6 与 9 小时尚可）----------

SLEEP_BANDS = ((7.0, 8.0, 100), (6.0, 7.0, 85), (8.0, 9.0, 85), (5.0, 6.0, 60), (9.0, 10.0, 60))
SLEEP_OUT_OF_RANGE = 30

# ---------- 趋势 ----------

TREND_RULES = (
    ("hr_avg", "平均心率", 5.0, "up", "次/分"),
    ("spo2_avg", "平均血氧", 1.5, "down", "%"),
    ("systolic_avg", "平均收缩压", 8.0, "up", "mmHg"),
    ("sleep_hours", "睡眠时长", 1.0, "down", "小时"),
)
TREND_PENALTY = 25

# ---------- 置信度 ----------

REFERENCE_CONFIDENCE = 0.6


@dataclass
class VitalReading:
    value: float
    at: datetime


@dataclass
class ScoreInputs:
    """评分所需的全部原材料，都是普通数据，方便离线测试。"""

    now: datetime = field(default_factory=datetime.now)
    age: int | None = None
    diseases: list[str] = field(default_factory=list)
    mobility: str = "good"
    # 字段 -> 最近 10 分钟内的可信读数
    current: dict[str, float] = field(default_factory=dict)
    # 字段 -> 24 小时内全部可信读数（含时间），按时间升序
    readings: dict[str, list[VitalReading]] = field(default_factory=dict)
    steps_today: int | None = None
    outings_last_week: int | None = None
    sleep_hours: float | None = None
    # 近 14 天（不含今天）每日静息心率与血氧中位数
    resting_hr_history: list[float] = field(default_factory=list)
    spo2_history: list[float] = field(default_factory=list)
    # (字段, 近 3 天中位数, 前期中位数)
    trend_pairs: list[tuple[str, float, float]] = field(default_factory=list)
    has_trend_history: bool = False
    yesterday_score: float | None = None
    # 最近一条读数距现在的分钟数，用于置信度
    data_age_minutes: float | None = None

    def values(self, key: str) -> list[float]:
        return [r.value for r in self.readings.get(key, [])]

    def has_disease(self, *names: str) -> bool:
        return any(d in self.diseases for d in names)


def grade_of(score: float) -> str:
    for threshold, grade in GRADES:
        if score >= threshold:
            return grade
    return GRADES[-1][1]


def _dim(key: str, score: float | None, reasons: list[str], detail: dict | None = None) -> HealthScoreDimension:
    return HealthScoreDimension(
        key=key,
        label=LABELS[key],
        weight=WEIGHTS[key],
        score=None if score is None else round(max(0.0, min(100.0, score)), 1),
        reasons=reasons,
        detail=detail or {},
    )


def _shown(key: str, value: float) -> str:
    return f"{format_vital(key, value)}{VITAL_UNITS.get(key, '')}"


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * pct)))
    return ordered[index]


def resting_heart_rate(values: list[float]) -> float | None:
    """静息心率取当天可信心率的第 10 百分位，至少要 10 个读数。"""
    if len(values) < 10:
        return None
    return round(_percentile(values, 0.10), 1)


# ---------- 各维度 ----------


def _score_acute(inputs: ScoreInputs) -> HealthScoreDimension:
    scale2 = inputs.has_disease(*SCALE2_DISEASES)
    keys = ("heart_rate", "spo2", "systolic", "temperature")
    measured = [k for k in keys if k in inputs.current or inputs.readings.get(k)]
    if not measured:
        return _dim("acute", None, ["今天还没有体征读数"])

    total = 0.0
    worst_single = 0.0
    reasons = []
    for key in measured:
        current_points = 0
        if key in inputs.current:
            current_points = news_points(key, inputs.current[key], scale2 and key == "spo2")
        # 今日峰值随时间衰减
        peak_points, peak_at, peak_value = 0, None, None
        for reading in inputs.readings.get(key, []):
            points = news_points(key, reading.value, scale2 and key == "spo2")
            if points > peak_points or (points == peak_points and points and peak_at and reading.at > peak_at):
                peak_points, peak_at, peak_value = points, reading.at, reading.value
        decayed = 0.0
        if peak_points and peak_at is not None:
            hours = max(0.0, (inputs.now - peak_at).total_seconds() / 3600)
            decayed = peak_points * 0.5 ** (hours / ACUTE_HALF_LIFE_HOURS)
        effective = max(float(current_points), decayed)
        total += effective
        worst_single = max(worst_single, effective)

        label = VITAL_LABELS[key]
        if current_points >= 2:
            reasons.append(f"{label} {_shown(key, inputs.current[key])} 明显异常（NEWS2 记 {current_points} 分）")
        elif current_points == 1:
            reasons.append(f"{label} {_shown(key, inputs.current[key])} 略偏离")
        elif decayed >= 0.5 and peak_value is not None:
            reasons.append(f"今天 {peak_at:%H:%M} {label}曾达 {_shown(key, peak_value)}，影响还在消退")

    score = _interp(NEWS_TO_SCORE, total)
    return _dim(
        "acute",
        score,
        reasons,
        {"news_total": round(total, 2), "worst_single": round(worst_single, 2), "measured": len(measured), "scale2": scale2},
    )


def _score_control(inputs: ScoreInputs) -> HealthScoreDimension:
    parts: list[float] = []
    reasons: list[str] = []
    detail: dict = {}

    systolic_values = inputs.values("systolic")
    diastolic_values = inputs.values("diastolic")
    if systolic_values:
        target = systolic_target(inputs.age)
        sys_med = median(systolic_values)
        detail["systolic_median"] = round(sys_med)
        detail["systolic_target"] = target
        if sys_med < 100:
            sys_score = 60
            reasons.append(f"血压偏低（今日收缩压中位 {sys_med:.0f}），起身时慢一点")
        elif sys_med < target:
            sys_score = 100
        elif sys_med < target + 20:
            sys_score = 70
            reasons.append(f"收缩压中位 {sys_med:.0f}，高于{inputs.age or ''}岁目标 {target}")
        elif sys_med < target + 40:
            sys_score = 40
            reasons.append(f"收缩压中位 {sys_med:.0f}，明显高于目标 {target}")
        else:
            sys_score = 15
            reasons.append(f"收缩压中位 {sys_med:.0f}，严重超标，建议就医复查")
        dia_score = 100
        if diastolic_values:
            dia_med = median(diastolic_values)
            detail["diastolic_median"] = round(dia_med)
            if dia_med >= 110:
                dia_score = 15
            elif dia_med >= 100:
                dia_score = 40
            elif dia_med >= 90:
                dia_score = 70
            if dia_score < 100:
                reasons.append(f"舒张压中位 {dia_med:.0f}，高于目标 90")
        parts.append(min(sys_score, dia_score))

    hr_values = inputs.values("heart_rate")
    resting = resting_heart_rate(hr_values)
    if resting is not None:
        detail["resting_hr"] = resting
        low, high = RESTING_HR_RANGE
        if low <= resting <= high:
            hr_score = 100
        elif resting > 100:
            hr_score = 40
            reasons.append(f"今日静息心率 {resting:.0f}，偏快")
        elif resting > high:
            hr_score = 70
            reasons.append(f"今日静息心率 {resting:.0f}，略高于 {high}")
        elif resting < 45:
            hr_score = 40
            reasons.append(f"今日静息心率 {resting:.0f}，偏慢")
        else:
            hr_score = 70
            reasons.append(f"今日静息心率 {resting:.0f}，略低于 {low}")
        parts.append(hr_score)

    spo2_values = inputs.values("spo2")
    if spo2_values:
        target = SPO2_TARGET_SCALE2 if inputs.has_disease(*SCALE2_DISEASES) else SPO2_TARGET
        spo2_med = median(spo2_values)
        detail["spo2_median"] = round(spo2_med)
        detail["spo2_target"] = target
        gap = target - spo2_med
        if gap <= 0:
            spo2_score = 100
        elif gap <= 2:
            spo2_score = 70
            reasons.append(f"今日血氧中位 {spo2_med:.0f}%，略低于目标 {target}%")
        else:
            spo2_score = 40
            reasons.append(f"今日血氧中位 {spo2_med:.0f}%，低于目标 {target}%")
        parts.append(spo2_score)

    if not parts:
        return _dim("control", None, ["今天还没有足够的血压、心率或血氧读数"])
    # 最差一项与平均各占一半：血压 172/104 不能被正常的心率血氧平均掉
    score = 0.5 * min(parts) + 0.5 * sum(parts) / len(parts)
    return _dim("control", score, reasons, detail)


def _score_baseline(inputs: ScoreInputs) -> HealthScoreDimension:
    parts: list[float] = []
    reasons: list[str] = []
    detail: dict = {}

    resting = resting_heart_rate(inputs.values("heart_rate"))
    if resting is not None and len(inputs.resting_hr_history) >= BASELINE_MIN_DAYS:
        baseline = median(inputs.resting_hr_history)
        deviation = resting - baseline
        detail.update({"resting_hr": resting, "resting_hr_baseline": round(baseline, 1), "deviation": round(deviation, 1)})
        if deviation <= -10:
            score = 70
            reasons.append(f"静息心率 {resting:.0f}，比平时低了 {abs(deviation):.0f}，留意有没有乏力")
        else:
            score = _interp(RHR_DEVIATION, max(0.0, deviation))
            if deviation > 5:
                reasons.append(f"静息心率 {resting:.0f}，比自己平时的 {baseline:.0f} 高了 {deviation:.0f}")
        parts.append(score)

    spo2_values = inputs.values("spo2")
    if spo2_values and len(inputs.spo2_history) >= BASELINE_MIN_DAYS:
        today = median(spo2_values)
        baseline = median(inputs.spo2_history)
        drop = baseline - today
        detail.update({"spo2_median": round(today), "spo2_baseline": round(baseline, 1)})
        if drop >= 5:
            parts.append(20)
            reasons.append(f"血氧比平时低了 {drop:.0f} 个点")
        elif drop >= 3:
            parts.append(50)
            reasons.append(f"血氧比平时低了 {drop:.0f} 个点")
        else:
            parts.append(100)

    if not parts:
        return _dim("baseline", None, [f"个人基线需要至少 {BASELINE_MIN_DAYS} 天的历史数据，仍在积累"])
    return _dim("baseline", sum(parts) / len(parts), reasons, detail)


def _score_stability(inputs: ScoreInputs, classify) -> HealthScoreDimension:
    total = sum(len(v) for v in inputs.readings.values())
    if total < 10:
        return _dim("stability", None, ["24 小时内采样点不足，暂不评估稳定性"])
    abnormal = sum(
        1
        for key, readings in inputs.readings.items()
        for r in readings
        if classify(key, r.value) != "normal"
    )
    ratio = abnormal / total
    ratio_score = max(0.0, 100.0 - ratio * 400.0)
    reasons = []
    if ratio >= 0.05:
        reasons.append(f"24 小时内 {ratio:.0%} 的读数偏离正常范围")

    hr = inputs.values("heart_rate")
    volatility_score = None
    if len(hr) >= 10:
        mean = sum(hr) / len(hr)
        cv = pstdev(hr) / mean if mean else 0.0
        volatility_score = max(0.0, min(100.0, 100.0 - (cv - 0.08) / 0.17 * 100.0))
        if cv > 0.15:
            reasons.append(f"心率波动较大（变异系数 {cv:.0%}）")
    score = ratio_score if volatility_score is None else ratio_score * 0.6 + volatility_score * 0.4
    return _dim("stability", score, reasons, {"abnormal_ratio": round(ratio, 3), "points": total})


def _score_activity(inputs: ScoreInputs) -> HealthScoreDimension:
    parts = []
    reasons = []
    detail: dict = {}
    if inputs.steps_today is not None:
        factor = MOBILITY_FACTOR.get(inputs.mobility, 1.0)
        equivalent = inputs.steps_today / factor
        step_score = _interp(STEP_CURVE, equivalent)
        parts.append((step_score, 0.7))
        plateau = round(6000 * factor)
        detail.update({"steps_today": inputs.steps_today, "plateau_steps": plateau})
        if step_score < 50:
            reasons.append(f"今日步数 {inputs.steps_today}，离 {plateau} 步的健康平台期还远")
        elif step_score < 90:
            reasons.append(f"今日步数 {inputs.steps_today}，再走走就到 {plateau} 步了")
    if inputs.outings_last_week is not None:
        outing_score = min(100.0, inputs.outings_last_week / OUTING_GOAL_PER_WEEK * 100.0)
        parts.append((outing_score, 0.3))
        detail["outings_last_week"] = inputs.outings_last_week
        if inputs.outings_last_week == 0:
            reasons.append("近 7 天没有外出记录")
    if not parts:
        return _dim("activity", None, ["没有步数与外出数据"])
    weight = sum(w for _, w in parts)
    return _dim("activity", sum(v * w for v, w in parts) / weight, reasons, detail)


def _score_sleep(inputs: ScoreInputs) -> HealthScoreDimension:
    hours = inputs.sleep_hours
    if hours is None:
        return _dim("sleep", None, ["手环未提供睡眠数据"])
    score = float(SLEEP_OUT_OF_RANGE)
    for low, high, band in SLEEP_BANDS:
        if low <= hours < high:
            score = float(band)
            break
    if hours == 8.0:
        score = 100.0
    reasons = []
    if hours < 6:
        reasons.append(f"昨夜睡眠仅 {hours:.1f} 小时，65 岁以上建议 7~8 小时")
    elif hours >= 9:
        reasons.append(f"昨夜睡眠 {hours:.1f} 小时，明显偏长")
    return _dim("sleep", score, reasons, {"hours": hours})


def _score_trend(inputs: ScoreInputs) -> HealthScoreDimension:
    if not inputs.has_trend_history:
        return _dim("trend", None, ["日摘要不足两天，暂无趋势"])
    reasons = []
    penalties = 0
    rules = {key: (label, threshold, worse_dir, unit) for key, label, threshold, worse_dir, unit in TREND_RULES}
    for key, recent, earlier in inputs.trend_pairs:
        if key not in rules:
            continue
        label, threshold, worse_dir, unit = rules[key]
        delta = recent - earlier
        if abs(delta) < threshold:
            continue
        worse = delta > 0 if worse_dir == "up" else delta < 0
        if worse:
            penalties += 1
            direction = "上升" if delta > 0 else "下降"
            reasons.append(f"近 3 日{label}较前期{direction} {abs(round(delta, 1))}{unit}")
    return _dim("trend", 100.0 - penalties * TREND_PENALTY, reasons, {"worsening": penalties})


# ---------- 合成 ----------


def _default_classify(key: str, value: float) -> str:
    """离线测试时没有个性化阈值，退回 NEWS2：记分即视为异常。"""
    return "normal" if news_points(key, value) == 0 else "warning"


def _confidence(available: int, total: int, data_age_minutes: float | None) -> float:
    coverage = available / total if total else 0.0
    if data_age_minutes is None:
        freshness = 0.4
    elif data_age_minutes <= 10:
        freshness = 1.0
    elif data_age_minutes <= 60:
        freshness = 0.8
    elif data_age_minutes <= 360:
        freshness = 0.6
    else:
        freshness = 0.4
    return round(coverage * freshness, 2)


def compute_score(inputs: ScoreInputs, classify=None) -> HealthScoreOut:
    """纯函数：从原材料算出评分。classify(key, value) 用个性化阈值判定读数是否正常。"""
    classify = classify or _default_classify
    dimensions = [
        _score_acute(inputs),
        _score_control(inputs),
        _score_baseline(inputs),
        _score_stability(inputs, classify),
        _score_activity(inputs),
        _score_sleep(inputs),
        _score_trend(inputs),
    ]
    available = [d for d in dimensions if d.score is not None]
    confidence = _confidence(len(available), len(dimensions), inputs.data_age_minutes)
    if not available:
        return HealthScoreOut(
            score=None,
            grade="",
            coverage=f"0/{len(dimensions)}",
            dimensions=dimensions,
            deductions=[],
            confidence=0.0,
            is_reference=True,
            summary="暂无足够数据评分",
            computed_at=inputs.now,
        )

    total_weight = sum(d.weight for d in available)
    score = sum(d.score * d.weight for d in available) / total_weight

    # NEWS2 的临床响应阈值：单项 3 分需紧急评估，合计 5~6 分中风险，≥7 分高风险
    acute = dimensions[0].detail
    news_total = acute.get("news_total", 0.0)
    cap_reason = ""
    if news_total >= 7:
        score, cap_reason = min(score, CAP_TOTAL_7), "NEWS2 合计 ≥7 分，属高风险"
    elif news_total >= 5:
        score, cap_reason = min(score, CAP_TOTAL_5), "NEWS2 合计 5~6 分，属中风险"
    elif acute.get("worst_single", 0.0) >= 3:
        score, cap_reason = min(score, CAP_SINGLE_PARAM_3), "有一项体征达 NEWS2 单项 3 分"
    score_int = int(round(max(0.0, score)))

    losses = []
    for dim in available:
        loss = (100.0 - dim.score) * dim.weight / total_weight
        for reason in dim.reasons:
            losses.append((loss, reason))
    losses.sort(key=lambda item: -item[0])
    deductions: list[str] = []
    for _, reason in losses:
        if reason not in deductions:
            deductions.append(reason)
        if len(deductions) == 3:
            break

    grade = grade_of(score_int)
    delta = None
    if inputs.yesterday_score is not None:
        delta = int(round(score_int - inputs.yesterday_score))
    is_reference = confidence < REFERENCE_CONFIDENCE

    return HealthScoreOut(
        score=score_int,
        grade=grade,
        coverage=f"{len(available)}/{len(dimensions)}",
        dimensions=dimensions,
        deductions=deductions,
        delta_vs_yesterday=delta,
        confidence=confidence,
        is_reference=is_reference,
        cap_reason=cap_reason,
        summary=_summary(score_int, grade, deductions, len(available), len(dimensions), cap_reason, is_reference),
        friendly_text=_friendly(score_int, grade, deductions, is_reference),
        computed_at=inputs.now,
    )


def _summary(score, grade, deductions, used, total, cap_reason, is_reference) -> str:
    text = f"健康评分 {score} 分（{grade}），基于 {used}/{total} 项维度"
    if is_reference:
        text += "，数据不全仅供参考"
    if cap_reason:
        text += f"；{cap_reason}，总分受限"
    if deductions:
        text += "；主要扣分：" + "、".join(deductions[:2])
    return text


def _friendly(score, grade, deductions, is_reference) -> str:
    """给老人和家属看的说法：先说好消息，扣分只点最重要的一条，不吓人。"""
    opening = {"优": "今天状态很好", "良": "今天整体不错", "中": "今天状态一般", "差": "今天需要多留意"}[grade]
    text = f"{opening}，健康评分 {score} 分" + ("（数据还不全，先做参考）。" if is_reference else "。")
    if deductions:
        text += f"可以关注一下：{deductions[0]}。"
    elif grade == "优":
        text += "各项指标都很平稳。"
    return text


# ---------- 从数据库收集原材料 ----------


async def gather_inputs(
    db: AsyncSession,
    elder: Elder,
    assessment: HealthAssessment,
    records: list[HealthRecord],
    rules: dict | None = None,
) -> tuple[ScoreInputs, object]:
    """records 为 24 小时内按时间升序的记录（与 build_assessment 共用）。返回 (原材料, 个性化判定函数)。"""
    from app.services import health_service  # 局部导入避免循环依赖

    rules = rules or await get_thresholds_for(db, elder)
    now = datetime.now()
    inputs = ScoreInputs(
        now=now,
        age=elder.age,
        diseases=[str(d).strip() for d in (elder.chronic_diseases or [])],
        mobility=elder.mobility_level or "good",
    )

    for key in VITAL_LABELS:
        stat = assessment.vitals.get(key)
        if stat is not None and stat.latest is not None:
            inputs.current[key] = float(stat.latest)
        inputs.readings[key] = [
            VitalReading(value=float(r.trusted(key)), at=r.recorded_at)
            for r in records
            if r.trusted(key) is not None
        ]
    if records:
        inputs.data_age_minutes = max(0.0, (now - records[-1].recorded_at).total_seconds() / 60)

    inputs.steps_today = assessment.steps_today
    inputs.sleep_hours = assessment.sleep_hours_last_night

    week_ago = now - timedelta(days=7)
    outings = await db.execute(
        select(func.count(OutingTask.id)).where(
            OutingTask.elder_id == elder.id,
            OutingTask.started_at >= week_ago,
            OutingTask.status != OutingTaskStatus.ABORTED,
        )
    )
    inputs.outings_last_week = int(outings.scalar() or 0)

    summaries = await health_service.list_summaries(db, elder.id, days=BASELINE_DAYS + 1)
    history = [s for s in summaries if s.summary_date < date.today()]
    inputs.resting_hr_history = [s.resting_hr for s in history if s.resting_hr is not None]
    inputs.spo2_history = [s.spo2_median for s in history if s.spo2_median is not None]

    if len(summaries) >= 2:
        inputs.has_trend_history = True
        recent = summaries[-3:]
        earlier = summaries[:-3] or summaries[:1]
        for key, *_ in TREND_RULES:
            recent_values = [getattr(s, key) for s in recent if getattr(s, key) is not None]
            earlier_values = [getattr(s, key) for s in earlier if getattr(s, key) is not None]
            if recent_values and earlier_values:
                inputs.trend_pairs.append((key, median(recent_values), median(earlier_values)))

    yesterday = date.today() - timedelta(days=1)
    for summary in history:
        if summary.summary_date == yesterday and summary.health_score is not None:
            inputs.yesterday_score = summary.health_score

    def classify(key: str, value: float) -> str:
        return health_service._classify(key, value, rules)

    return inputs, classify


# elder_id -> 上次刷新当日评分的时刻，避免每个遥测包都重算一遍 24 小时
_last_refresh: dict[int, float] = {}
REFRESH_MIN_INTERVAL_SECONDS = 30.0


async def refresh(db: AsyncSession, elder: Elder, force: bool = False) -> HealthScoreOut | None:
    """按 24 小时口径重算并写入当日评分。上报路径上调用时按老人节流。"""
    from app.services import health_service  # 局部导入避免循环依赖

    now = time.monotonic()
    if not force and now - _last_refresh.get(elder.id, 0.0) < REFRESH_MIN_INTERVAL_SECONDS:
        return None
    _last_refresh[elder.id] = now
    assessment = await health_service.build_assessment(db, elder, 24, with_score=True)
    return assessment.health_score


async def persist_daily_score(db: AsyncSession, elder_id: int, score: HealthScoreOut) -> None:
    """把当日评分写进日摘要，供首页快速读取和画趋势。"""
    if score.score is None:
        return
    result = await db.execute(
        select(HealthDailySummary).where(
            HealthDailySummary.elder_id == elder_id,
            HealthDailySummary.summary_date == date.today(),
        )
    )
    summary = result.scalars().first()
    if summary is None:
        summary = HealthDailySummary(elder_id=elder_id, summary_date=date.today())
        db.add(summary)
    summary.health_score = float(score.score)
    summary.health_grade = score.grade
    summary.health_confidence = score.confidence
    await db.flush()
