import json
import re
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompts import OUTING_SYSTEM_PROMPT, build_outing_user_prompt
from app.ai.base import ChatMessage, ChatRequest
from app.ai.factory import get_llm
from app.ai.mock import TASK_OUTING
from app.core.logging import get_logger
from app.models import Elder, OutingDecision, OutingRequest
from app.models.enums import OutingDecisionType
from app.rules.engine import evaluate_rules
from app.schemas.outing import OutingEvaluation, OutingLimits, RuleReport
from app.services import (
    device_service,
    health_service,
    outing_service,
    prompt_service,
    weather_service,
)

logger = get_logger(__name__)

JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(raw: str) -> dict | None:
    match = JSON_BLOCK.search(raw or "")
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _weather_text(weather: dict) -> str:
    parts = [f"{weather.get('city', '')}今天{weather.get('condition', '')}"]
    if weather.get("temperature") is not None:
        parts.append(f"气温{weather['temperature']}℃")
    if weather.get("aqi") is not None:
        parts.append(f"空气质量指数{weather['aqi']}")
    return "，".join(parts)


def _elder_profile(elder: Elder) -> dict:
    return {
        "姓名": elder.name,
        "年龄": elder.age,
        "性别": elder.gender,
        "慢性病": elder.chronic_diseases or [],
        "行动能力": {"good": "良好", "limited": "行动稍有不便", "poor": "行动困难"}.get(
            elder.mobility_level, elder.mobility_level
        ),
        "独居": elder.live_alone,
    }


def _health_context(health) -> dict:
    return {
        "最近采集时间": health.latest_at.strftime("%H:%M") if health.latest_at else None,
        "指标": {
            key: {
                "最新值": stat.latest,
                "单位": stat.unit,
                "状态": stat.status,
                "24小时区间": [stat.min, stat.max],
            }
            for key, stat in health.vitals.items()
        },
        "昨夜睡眠小时": health.sleep_hours_last_night,
        "今日步数": health.steps_today,
        "异常项": health.abnormal_items,
        "趋势": health.trend_notes,
        "综合风险评分": health.risk_score,
    }


def _fallback_speech(decision: str, reasons: list[str], limits: OutingLimits) -> str:
    if decision == OutingDecisionType.DENY:
        return "今天先别出门了。" + ("；".join(reasons[:2]) if reasons else "身体状况不太合适。")
    if decision == OutingDecisionType.CONDITIONAL:
        return (
            "可以出去走走，不过要注意"
            + ("；".join(reasons[:2]) if reasons else "别走太远")
            + f"。建议{limits.duration_minutes}分钟内回来，不要超出{limits.radius_meters}米。"
        )
    return (
        f"今天身体状况不错，可以出去走走。建议{limits.duration_minutes}分钟左右回来，"
        f"在{limits.radius_meters}米范围内活动，我跟着您。"
    )


def _apply_guardrails(
    parsed: dict, report: RuleReport
) -> tuple[str, list[str], list[str], OutingLimits, float, bool]:
    """规则层不可被模型推翻: 模型只能比规则更保守。"""
    model_decision = str(parsed.get("decision", "")).lower()
    if model_decision not in (
        OutingDecisionType.ALLOW,
        OutingDecisionType.CONDITIONAL,
        OutingDecisionType.DENY,
    ):
        model_decision = ""

    if report.blocked:
        rule_decision = OutingDecisionType.DENY
    elif report.warn_hits:
        rule_decision = OutingDecisionType.CONDITIONAL
    else:
        rule_decision = OutingDecisionType.ALLOW

    severity = {
        OutingDecisionType.ALLOW: 0,
        OutingDecisionType.CONDITIONAL: 1,
        OutingDecisionType.DENY: 2,
    }
    overridden = False
    decision = rule_decision
    if model_decision:
        if severity[model_decision] > severity[rule_decision]:
            # 模型更保守, 采纳模型结论
            decision = model_decision
        elif severity[model_decision] < severity[rule_decision]:
            overridden = True

    reasons = [str(r) for r in parsed.get("reasons", []) if str(r).strip()]
    if not reasons:
        source_hits = report.block_hits if report.blocked else report.warn_hits
        reasons = [h.detail for h in source_hits] or ["各项健康指标正常"]
    advice = [str(a) for a in parsed.get("advice", []) if str(a).strip()]

    raw_limits = parsed.get("limits") or {}
    if decision == OutingDecisionType.DENY:
        limits = OutingLimits(
            duration_minutes=0, radius_meters=0, need_follow=False, return_before=""
        )
    else:
        duration = int(raw_limits.get("duration_minutes") or report.suggested_duration_minutes)
        radius = int(raw_limits.get("radius_meters") or report.suggested_radius_m)
        limits = OutingLimits(
            duration_minutes=max(10, min(duration, report.suggested_duration_minutes)),
            radius_meters=max(200, min(radius, report.suggested_radius_m)),
            need_follow=True,
            return_before=(
                datetime.now()
                + timedelta(
                    minutes=min(duration, report.suggested_duration_minutes)
                )
            ).strftime("%H:%M"),
        )

    confidence = float(parsed.get("confidence") or 0.85)
    return decision, reasons, advice, limits, min(max(confidence, 0.0), 1.0), overridden


def _clean_list(value, limit: int = 6) -> list[str]:
    if not isinstance(value, list):
        return []
    items = []
    for item in value:
        text = str(item).strip()
        if text and text not in items:
            items.append(text)
    return items[:limit]


def _model_extras(parsed: dict, report: RuleReport) -> dict:
    """智能体的补充输出；模型没给的用规则层的材料补上，保证话术里总有可执行的建议。"""
    expected = parsed.get("expected_minutes")
    try:
        expected = int(expected) if expected not in (None, "", 0) else None
    except (TypeError, ValueError):
        expected = None
    preparation = _clean_list(parsed.get("preparation"))
    if not preparation and not report.blocked:
        # 模型没列清单时用规则层的提示顶上；不出门就不需要清单
        preparation = _clean_list(report.advisories, limit=4)
    return {
        "purpose": str(parsed.get("purpose") or "").strip()[:40],
        "expected_minutes": expected,
        "preparation": preparation,
        "route_hint": str(parsed.get("route_hint") or "").strip()[:80],
        "best_time_hint": str(parsed.get("best_time_hint") or "").strip()[:60]
        or (f"{report.best_time_hint} 以后再看看" if report.best_time_hint else ""),
        "care_note": str(parsed.get("care_note") or "").strip()[:80],
    }


async def evaluate_outing(
    db: AsyncSession, elder: Elder, question: str = "我想出去走走"
) -> tuple[RuleReport, OutingEvaluation, dict]:
    """外出评估主流程: 采集事实 -> 规则判定 -> 模型生成结论与话术 -> 护栏校正。"""
    health = await health_service.build_assessment(db, elder)
    stats = await outing_service.get_stats(db, elder.id)
    # 外出要走一段时间，除了实况还要预报、预警、日落和生活指数
    weather = await weather_service.get_outing_weather(
        elder.city, elder.home_lat, elder.home_lng
    )
    devices = await device_service.device_status_snapshot(db, elder.id)
    report, _rules = await evaluate_rules(db, elder, health, stats, weather, devices)

    score = health.health_score
    context = {
        "question": question,
        "elder_name": elder.name,
        "elder_profile": _elder_profile(elder),
        "health": _health_context(health),
        "health_summary": health.summary_text,
        "health_score": (
            {
                "分数": score.score,
                "等级": score.grade,
                "扣分原因": score.deductions,
                "覆盖维度": score.coverage,
            }
            if score and score.score is not None
            else {}
        ),
        "stats": stats.model_dump(mode="json"),
        "weather": weather,
        "weather_text": _weather_text(weather),
        "devices": devices,
        "rule_report": report.model_dump(mode="json"),
        "now_text": datetime.now().strftime("%m月%d日 %H:%M"),
        "return_before": (
            datetime.now() + timedelta(minutes=report.suggested_duration_minutes)
        ).strftime("%H:%M"),
    }

    system_prompt = await prompt_service.get_template(db, "outing_system_prompt")
    llm = get_llm()
    raw = await llm.chat(
        ChatRequest(
            messages=[
                ChatMessage("system", system_prompt or OUTING_SYSTEM_PROMPT),
                ChatMessage("user", build_outing_user_prompt(context)),
            ],
            temperature=0.2,
            json_mode=True,
            task=TASK_OUTING,
            context=context,
        )
    )
    parsed = _extract_json(raw) or {}
    if not parsed:
        logger.warning("模型输出无法解析为 JSON, 使用规则结论兜底")

    decision, reasons, advice, limits, confidence, overridden = _apply_guardrails(
        parsed, report
    )
    speech = str(parsed.get("speech_text", "")).strip()
    if overridden or not speech:
        speech = _fallback_speech(decision, reasons, limits)
    extras = _model_extras(parsed, report)
    if decision == OutingDecisionType.DENY:
        # 不出门就没有路线可说，但"几点再看看"和准备清单要留着
        extras["route_hint"] = ""

    evaluation = OutingEvaluation(
        decision=decision,
        reasons=reasons,
        advice=advice,
        limits=limits,
        speech_text=speech,
        confidence=confidence,
        overridden_by_rules=overridden,
        llm_raw=raw,
        llm_provider=llm.name,
        **extras,
    )
    if overridden:
        logger.warning(
            "模型结论(%s)被规则层否决为 %s", parsed.get("decision"), decision
        )

    snapshots = {
        "health": health.model_dump(mode="json"),
        "weather": weather,
        "stats": stats.model_dump(mode="json"),
        "devices": devices,
    }
    return report, evaluation, snapshots


async def evaluate_and_persist(
    db: AsyncSession,
    elder: Elder,
    question: str,
    channel: str = "voice",
    device_id: str = "",
) -> tuple[OutingRequest, OutingDecision, RuleReport, OutingEvaluation]:
    report, evaluation, snapshots = await evaluate_outing(db, elder, question)
    request = await outing_service.create_request(
        db, elder.id, question, channel=channel, device_id=device_id
    )
    decision = await outing_service.save_decision(
        db,
        request,
        evaluation,
        report,
        health_snapshot=snapshots["health"],
        weather_snapshot=snapshots["weather"],
        outing_stats=snapshots["stats"],
    )
    return request, decision, report, evaluation
