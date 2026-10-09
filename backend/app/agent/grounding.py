"""把老人的体征数据讲成人话，供智能体引用。

智能体说"您身体还不错"是没有信息量的。这里按这位老人个性化后的阈值，把每一项读数
翻译成"体温 39.0℃，过高了，比正常上限 37.3℃ 高 1.7℃"这样的句子：有数值、有方向、有程度。
异常项逐条说，正常项一句带过。健康问询拿不到模型回答时，也能直接用这些句子兜底。
"""

from app.core.formatting import format_vital
from app.schemas.health import HealthAssessment

VITAL_ORDER = ("temperature", "heart_rate", "spo2", "systolic", "diastolic")
LABELS = {"heart_rate": "心率", "spo2": "血氧", "systolic": "收缩压", "diastolic": "舒张压", "temperature": "体温"}
UNITS = {"heart_rate": "次/分", "spo2": "%", "systolic": "mmHg", "diastolic": "mmHg", "temperature": "℃"}


def _fmt(key: str, value: float) -> str:
    return f"{format_vital(key, value)}{UNITS[key]}"


def _bounds(key: str, rules: dict) -> tuple[float | None, float | None, float | None, float | None]:
    """返回 (提醒下限, 提醒上限, 禁止下限, 禁止上限)，没有的边界为 None。"""
    if key == "heart_rate":
        cfg = rules["heart_rate"]
        return cfg["warn_min"], cfg["warn_max"], cfg["block_min"], cfg["block_max"]
    if key == "spo2":
        cfg = rules["spo2"]
        return cfg["warn_min"], None, cfg["block_min"], None
    if key == "systolic":
        cfg = rules["blood_pressure"]
        return cfg["systolic_warn_min"], cfg["systolic_warn_max"], cfg["systolic_block_min"], cfg["systolic_block_max"]
    if key == "diastolic":
        cfg = rules["blood_pressure"]
        return None, cfg["diastolic_warn_max"], None, cfg["diastolic_block_max"]
    if key == "temperature":
        cfg = rules["temperature"]
        return None, cfg["warn_max"], cfg["block_min"], cfg["block_max"]
    return None, None, None, None


def explain_vital(key: str, value: float, rules: dict) -> dict:
    """单项读数 -> {label, shown, status, direction, verdict, sentence}。"""
    label = LABELS[key]
    shown = _fmt(key, value)
    warn_min, warn_max, block_min, block_max = _bounds(key, rules)

    status, direction, verdict = "normal", "", "正常"
    if block_max is not None and value > block_max:
        status, direction = "danger", "high"
        verdict = f"过高了，比安全上限 {_fmt(key, block_max)} 还高 {_fmt(key, value - block_max)}"
        if key == "temperature":
            verdict = f"过高了，已经算发热，比正常上限 {_fmt(key, warn_max)} 高 {_fmt(key, value - warn_max)}"
    elif block_min is not None and value < block_min:
        status, direction = "danger", "low"
        verdict = f"过低了，比安全下限 {_fmt(key, block_min)} 还低 {_fmt(key, block_min - value)}"
    elif warn_max is not None and value > warn_max:
        status, direction = "warning", "high"
        verdict = f"偏高，正常不超过 {_fmt(key, warn_max)}，高了 {_fmt(key, value - warn_max)}"
    elif warn_min is not None and value < warn_min:
        status, direction = "warning", "low"
        verdict = f"偏低，正常不低于 {_fmt(key, warn_min)}，低了 {_fmt(key, warn_min - value)}"

    return {
        "key": key,
        "label": label,
        "value": value,
        "shown": shown,
        "status": status,
        "direction": direction,
        "verdict": verdict,
        "sentence": f"{label} {shown}，{verdict}" if status != "normal" else f"{label} {shown} 正常",
    }


def vitals_digest(health: HealthAssessment, rules: dict) -> dict:
    """把评估里的最新读数整理成可直接引用的句子。"""
    items = []
    recent_lines = []
    for key in VITAL_ORDER:
        stat = health.vitals.get(key)
        if stat is None or stat.latest is None:
            continue
        item = explain_vital(key, float(stat.latest), rules)
        if stat.recent_extreme is not None and item["status"] == "normal":
            # 现在正常、最近一小时曾越线：把峰值也按同一把尺子讲清楚，模型就不用自己算差多少
            peak = explain_vital(key, float(stat.recent_extreme), rules)
            recent_lines.append(
                f"{item['label']}最近一小时内曾到 {peak['shown']}，{peak['verdict']}，现在已回到 {item['shown']}"
            )
        items.append(item)

    abnormal = [i for i in items if i["status"] != "normal"]
    normal = [i for i in items if i["status"] == "normal"]
    # 血压两项都正常时合成一句，免得"收缩压正常、舒张压正常"重复
    normal_parts = []
    sys_ok = next((i for i in normal if i["key"] == "systolic"), None)
    dia_ok = next((i for i in normal if i["key"] == "diastolic"), None)
    for item in normal:
        if item["key"] in ("systolic", "diastolic") and sys_ok and dia_ok:
            continue
        normal_parts.append(f"{item['label']} {item['shown']}")
    if sys_ok and dia_ok:
        normal_parts.append(f"血压 {format_vital('systolic', sys_ok['value'])}/{format_vital('diastolic', dia_ok['value'])} mmHg")

    extras = []
    if health.sleep_hours_last_night is not None:
        hours = health.sleep_hours_last_night
        note = "够的" if 7 <= hours <= 9 else ("有点少" if hours < 7 else "有点多")
        extras.append(f"昨夜睡了 {hours} 小时，{note}")
    if health.steps_today is not None:
        extras.append(f"今天走了 {health.steps_today} 步")
    score = health.health_score
    score_line = ""
    if score is not None and score.score is not None:
        score_line = f"今日健康评分 {score.score} 分（{score.grade}）"
        if score.deductions:
            score_line += "，主要因为" + "、".join(score.deductions[:2])

    return {
        "measured_at": health.latest_at.strftime("%H:%M") if health.latest_at else "",
        "abnormal_lines": [i["sentence"] for i in abnormal],
        "recent_lines": recent_lines,
        "normal_summary": ("、".join(normal_parts) + " 都正常") if normal_parts else "",
        "extra_lines": extras,
        "score_line": score_line,
        "worst_status": "danger" if any(i["status"] == "danger" for i in abnormal) else "warning" if abnormal else "normal",
        "has_data": bool(items),
    }


def salutation(name: str, gender: str) -> str:
    """张三 + 男 -> 张爷爷。"""
    if not name:
        return "您"
    return name[0] + ("奶奶" if gender == "女" else "爷爷")


def spoken_health_report(name: str, gender: str, digest: dict) -> str:
    """健康问询的兜底回答：不依赖模型，也能把数据讲清楚、讲出高低。"""
    who = salutation(name, gender)
    if not digest.get("has_data"):
        return f"{who}，我这会儿还没测到您的数据，手环戴好了吗？等一会儿我再帮您看看。"
    parts = [f"{who}，"]
    abnormal = digest["abnormal_lines"]
    recent = digest.get("recent_lines") or []
    if abnormal:
        parts.append("；".join(abnormal) + "。")
        if digest["normal_summary"]:
            parts.append("其他的像" + digest["normal_summary"] + "。")
        if digest["worst_status"] == "danger":
            parts.append("这个数值不太对，您先坐下歇着，我建议联系家人或者去医院看看。")
        else:
            parts.append("先别急，您坐下休息一会儿，我过会儿再帮您测一次。")
    else:
        parts.append("您现在各项都挺好：" + digest["normal_summary"] + "。")
        if recent:
            parts.append("不过" + "；".join(recent) + "，您还是少走动、多歇歇。")
    if digest["extra_lines"]:
        parts.append("；".join(digest["extra_lines"]) + "。")
    return "".join(parts)


def mentions_numbers(text: str) -> bool:
    return any(ch.isdigit() for ch in text or "")
