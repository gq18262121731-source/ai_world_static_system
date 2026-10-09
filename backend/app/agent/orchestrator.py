import time
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import grounding
from app.agent import intent as intent_module
from app.agent.prompts import CONSULT_QA_SYSTEM_PROMPT, QA_SYSTEM_PROMPT, build_qa_user_prompt
from app.agent.tools import ToolBox
from app.ai.base import ChatMessage, ChatRequest
from app.ai.factory import get_llm, get_tts
from app.ai.mock import TASK_QA
from app.core.cache import cache
from app.core.events import hub
from app.core.formatting import format_vital
from app.core.logging import get_logger
from app.iot import commands
from app.models import Conversation, Elder, Message
from app.models.enums import (
    AlarmType,
    IntentType,
    MessageRole,
    OutingDecisionType,
)
from app.schemas.agent import AgentAction, AgentReply
from app.services import alarm_service, device_service, outing_service, prompt_service

logger = get_logger(__name__)

# 超过该时长没有对话则视为新会话
CONVERSATION_IDLE_MINUTES = 10
HISTORY_TURNS = 6


async def _get_conversation(
    db: AsyncSession, elder_id: int, device_id: str, conversation_id: int | None
) -> Conversation:
    if conversation_id:
        conversation = await db.get(Conversation, conversation_id)
        if conversation:
            return conversation
    deadline = datetime.now() - timedelta(minutes=CONVERSATION_IDLE_MINUTES)
    result = await db.execute(
        select(Conversation)
        .where(
            Conversation.elder_id == elder_id,
            Conversation.last_active_at >= deadline,
            Conversation.status == "open",
        )
        .order_by(Conversation.id.desc())
        .limit(1)
    )
    conversation = result.scalars().first()
    if conversation:
        return conversation
    now = datetime.now()
    conversation = Conversation(
        elder_id=elder_id,
        device_id=device_id,
        started_at=now,
        last_active_at=now,
    )
    db.add(conversation)
    await db.flush()
    return conversation


async def _add_message(
    db: AsyncSession,
    conversation: Conversation,
    role: str,
    text: str,
    intent: str = "",
    audio_url: str = "",
    latency_ms: int = 0,
    meta: dict | None = None,
) -> Message:
    message = Message(
        conversation_id=conversation.id,
        role=role,
        intent=intent,
        text=text,
        audio_url=audio_url,
        latency_ms=latency_ms,
        meta=meta or {},
        created_at=datetime.now(),
    )
    db.add(message)
    conversation.last_active_at = message.created_at
    await db.flush()
    return message


async def _history(elder_id: int) -> list[dict]:
    return await cache.get_json(f"dialog:{elder_id}") or []


async def _push_history(elder_id: int, question: str, answer: str) -> None:
    history = await _history(elder_id)
    history.append({"user": question, "assistant": answer})
    await cache.set_json(f"dialog:{elder_id}", history[-HISTORY_TURNS:], ex=1800)


def _vital_text(health, key: str) -> str:
    stat = health.vitals.get(key)
    if not stat or stat.latest is None:
        return ""
    status_text = {"normal": "正常", "warning": "略微偏高或偏低", "danger": "明显异常"}.get(
        stat.status, ""
    )
    return f"{stat.label}{format_vital(key, stat.latest)}{stat.unit}，{status_text}"


def _bp_text(health) -> str:
    sys_stat = health.vitals.get("systolic")
    dia_stat = health.vitals.get("diastolic")
    if not sys_stat or sys_stat.latest is None:
        return ""
    dia = (
        f"/{format_vital('diastolic', dia_stat.latest)}"
        if dia_stat and dia_stat.latest is not None
        else ""
    )
    status_text = {"normal": "在正常范围", "warning": "有点偏高", "danger": "明显偏高"}.get(
        sys_stat.status, ""
    )
    return f"血压 {format_vital('systolic', sys_stat.latest)}{dia} mmHg，{status_text}"


async def _qa_context(toolbox: ToolBox, question: str) -> dict:
    health = await toolbox.get_recent_health()
    weather = await toolbox.get_weather()
    stats = await toolbox.get_outing_stats()
    from app.rules.config_store import get_thresholds_for
    from app.services.evaluation_service import _weather_text

    verdict = "还算平稳"
    if any(v.status == "danger" for v in health.vitals.values()):
        verdict = "有指标明显异常，建议联系家人或去医院看看"
    elif any(v.status == "warning" for v in health.vitals.values()):
        verdict = "有一两项略微偏离，注意休息"

    # 按这位老人个性化后的阈值，把每项读数讲成"值 + 高低 + 程度"的句子
    rules = await get_thresholds_for(toolbox.db, toolbox.elder)
    digest = grounding.vitals_digest(health, rules)

    return {
        "question": question,
        "elder_name": toolbox.elder.name,
        "elder_gender": toolbox.elder.gender,
        "salutation": grounding.salutation(toolbox.elder.name, toolbox.elder.gender),
        "health_summary": health.summary_text,
        "health_verdict": verdict,
        "digest": digest,
        "heart_text": _vital_text(health, "heart_rate") or "暂时没有心率数据",
        "bp_text": _bp_text(health) or "暂时没有血压数据",
        "spo2_text": _vital_text(health, "spo2") or "暂时没有血氧数据",
        "sleep_text": (
            f"昨夜睡了 {health.sleep_hours_last_night} 小时"
            if health.sleep_hours_last_night
            else "暂时没有睡眠数据"
        ),
        "steps_text": (
            f"今天走了 {health.steps_today} 步"
            if health.steps_today is not None
            else "暂时没有步数数据"
        ),
        "weather_text": _weather_text(weather),
        "outing_text": f"今天已外出 {stats['today_count']} 次",
        "history": await _history(toolbox.elder.id),
    }


def _grounded_reply(reply: str, intent: str, context: dict) -> str:
    """健康问询必须带数字：模型答了一句空话就换成按数据讲解的兜底话术。"""
    if intent != IntentType.HEALTH_QUERY:
        return reply
    digest = context.get("digest") or {}
    if grounding.mentions_numbers(reply) or not digest.get("has_data"):
        return reply
    logger.info("健康问询的回答没有引用任何数据，改用数据讲解兜底")
    return grounding.spoken_health_report(
        context.get("elder_name", ""), context.get("elder_gender", ""), digest
    )


async def _qa_request(
    db: AsyncSession, toolbox: ToolBox, question: str, channel: str = "dog", intent: str = ""
) -> tuple[ChatRequest, dict]:
    context = await _qa_context(toolbox, question)
    context["channel"] = channel
    context["intent"] = intent
    context["fallback_answer"] = await prompt_service.get_template(db, "qa_fallback")
    if channel == CHANNEL_CONSOLE:
        system_prompt = await prompt_service.get_template(db, "consult_qa_system_prompt")
        default_prompt = CONSULT_QA_SYSTEM_PROMPT
    else:
        system_prompt = await prompt_service.get_template(db, "qa_system_prompt")
        default_prompt = QA_SYSTEM_PROMPT
    request = ChatRequest(
        messages=[
            ChatMessage("system", system_prompt or default_prompt),
            ChatMessage("user", build_qa_user_prompt(context)),
        ],
        temperature=0.4,
        max_tokens=600 if channel == CHANNEL_CONSOLE else 400,
        task=TASK_QA,
        context=context,
    )
    return request, context


async def _answer_question(
    db: AsyncSession, toolbox: ToolBox, question: str, intent: str = "", channel: str = "dog"
) -> str:
    request, context = await _qa_request(db, toolbox, question, channel, intent)
    reply = (await get_llm().chat(request)).strip()
    return _grounded_reply(reply, intent, context)


# 两条对话通道：机器狗（老人说话，回答要播报、可能开启跟随）
# 与健康咨询（老人在屏幕上打字问自己的情况，只回答文字，不播报、不动任何设备与任务）
CHANNEL_DOG = "dog"
CHANNEL_CONSOLE = "console"


async def handle_utterance_events(
    db: AsyncSession,
    elder: Elder,
    text: str,
    device_id: str = "",
    conversation_id: int | None = None,
    channel: str = CHANNEL_DOG,
):
    """语音交互主流程，按阶段产出事件，最后一条是完整回复。

    事件形状：
      {"event": "stage", "stage": "intent|reasoning|tts|speak", "detail": str, "ms": int}
      {"event": "intent", "intent": str, "confidence": float}
      {"event": "token", "text": str}          -- 问答类回复逐段流出
      {"event": "final", "reply": AgentReply}
    """
    started = time.perf_counter()
    stages: dict[str, int] = {}

    def _elapsed(since: float) -> int:
        return int((time.perf_counter() - since) * 1000)

    toolbox = ToolBox(db, elder)
    conversation = await _get_conversation(
        db, elder.id, device_id or (CHANNEL_CONSOLE if channel == CHANNEL_CONSOLE else ""), conversation_id
    )

    stage_start = time.perf_counter()
    pending_fall = await alarm_service.pending_fall_alarm(db, elder.id) if channel == CHANNEL_DOG else None
    detected_intent, intent_confidence = await intent_module.classify(
        text, in_fall_confirm=pending_fall is not None
    )
    if channel == CHANNEL_CONSOLE and detected_intent in (
        IntentType.SOS,
        IntentType.FALL_CONFIRM,
        IntentType.END_OUTING,
    ):
        # 值守人员在控制台里说"救命"不是老人在求助，按普通问答处理
        detected_intent = IntentType.GENERAL_QA
    stages["intent_ms"] = _elapsed(stage_start)
    yield {"event": "intent", "intent": detected_intent, "confidence": round(intent_confidence, 2)}
    yield {"event": "stage", "stage": "intent", "detail": detected_intent, "ms": stages["intent_ms"]}

    await _add_message(db, conversation, MessageRole.ELDER, text, intent=detected_intent)

    actions: list[AgentAction] = []
    decision_payload: dict | None = None
    task_id: int | None = None
    stage_start = time.perf_counter()
    reply_text = ""

    if detected_intent == IntentType.FALL_CONFIRM and pending_fall:
        elder_ok = intent_module.is_positive_reply(text)
        await alarm_service.resolve_fall_confirmation(db, pending_fall, elder_ok, text)
        reply_text = await prompt_service.get_template(
            db, "fall_reply_ok" if elder_ok else "fall_reply_help"
        )
        if not elder_ok:
            actions.append(AgentAction(type="alarm", payload={"alarm_id": pending_fall.id}))

    elif detected_intent == IntentType.SOS:
        alarm = await toolbox.raise_alarm(AlarmType.SOS, "老人主动求助", text or "老人通过机器狗求助")
        reply_text = await prompt_service.get_template(db, "sos_reply")
        actions.append(AgentAction(type="alarm", payload={"alarm_id": alarm.id}))

    elif detected_intent == IntentType.OUTING_REQUEST:
        yield {"event": "stage", "stage": "reasoning", "detail": "正在综合健康、天气与规则评估外出…"}
        if channel == CHANNEL_CONSOLE:
            # 控制台只是"看看他现在能不能出门"，不落申请、不开跟随
            from app.services import evaluation_service

            report, evaluation, _snapshots = await evaluation_service.evaluate_outing(db, elder, text)
            toolbox.used.append("evaluate_outing")
            decision_payload = {
                "decision": evaluation.decision,
                "reasons": evaluation.reasons,
                "advice": evaluation.advice,
                "limits": evaluation.limits.model_dump(),
                "overridden_by_rules": evaluation.overridden_by_rules,
                "extras": evaluation.extras(),
                "rule_report": report.model_dump(mode="json"),
                "preview": True,
            }
            # 健康咨询是老人自己在问，直接用给老人听的那段话；规则明细在决策面板里展开
            reply_text = evaluation.speech_text
        else:
            _request, decision, _report, evaluation = await toolbox.evaluate_outing(text)
            decision_payload = {
                "decision_id": decision.id,
                "decision": decision.decision,
                "reasons": decision.reasons,
                "advice": decision.advice,
                "limits": decision.limits,
                "overridden_by_rules": decision.overridden_by_rules,
                "extras": evaluation.extras(),
            }
            reply_text = evaluation.speech_text
            if decision.decision != OutingDecisionType.DENY:
                task = await toolbox.start_following(decision)
                if task:
                    task_id = task.id
                    actions.append(
                        AgentAction(
                            type="start_follow",
                            payload={
                                "task_id": task.id,
                                "duration_minutes": task.planned_duration_minutes,
                                "radius_m": task.geofence_radius_m,
                            },
                        )
                    )

    elif detected_intent == IntentType.END_OUTING:
        task = await toolbox.end_following("voice")
        if task:
            task_id = task.id
            reply_text = await prompt_service.render(db, "outing_end_reply", summary=task.summary)
            actions.append(AgentAction(type="stop_follow", payload={"task_id": task.id}))
        else:
            reply_text = await prompt_service.get_template(db, "outing_end_no_task")

    else:
        yield {"event": "stage", "stage": "reasoning", "detail": "正在调取健康、天气与外出数据…"}
        request, context = await _qa_request(db, toolbox, text, channel, detected_intent)
        chunks: list[str] = []
        async for chunk in get_llm().chat_stream(request):
            chunks.append(chunk)
            yield {"event": "token", "text": chunk}
        reply_text = _grounded_reply("".join(chunks).strip(), detected_intent, context)
        await _push_history(elder.id, text, reply_text)

    stages["reasoning_ms"] = _elapsed(stage_start)
    yield {"event": "stage", "stage": "reasoning", "detail": f"调取了 {len(toolbox.used)} 项数据", "ms": stages["reasoning_ms"]}
    latency_ms = _elapsed(started)

    audio_url = ""
    if channel == CHANNEL_DOG:
        stage_start = time.perf_counter()
        yield {"event": "stage", "stage": "tts", "detail": "正在合成语音…"}
        try:
            audio_url = await get_tts().synthesize(reply_text)
        except Exception as exc:
            logger.warning("语音合成失败: %s", exc)
        stages["tts_ms"] = _elapsed(stage_start)
        yield {"event": "stage", "stage": "tts", "detail": "语音就绪" if audio_url else "使用本地朗读", "ms": stages["tts_ms"]}

        dog_device_id = device_id
        if not dog_device_id:
            dog = await device_service.get_elder_device(db, elder.id, "dog")
            dog_device_id = dog.device_id if dog else ""
        if dog_device_id:
            await commands.speak(dog_device_id, reply_text, elder.id, audio_url)
            actions.insert(0, AgentAction(type="speak", payload={"text": reply_text}))
            yield {"event": "stage", "stage": "speak", "detail": f"已下发到 {dog_device_id}"}

    await _add_message(
        db,
        conversation,
        MessageRole.SYSTEM,
        reply_text,
        intent=detected_intent,
        audio_url=audio_url,
        latency_ms=latency_ms,
        meta={
            "tools": toolbox.used,
            "task_id": task_id,
            "decision": decision_payload,
            "intent_confidence": round(intent_confidence, 2),
            "stages": stages,
            "llm_provider": get_llm().name,
            "channel": channel,
        },
    )

    reply = AgentReply(
        conversation_id=conversation.id,
        elder_id=elder.id,
        intent=detected_intent,
        request_text=text,
        reply_text=reply_text,
        audio_url=audio_url,
        actions=actions,
        decision=decision_payload,
        task_id=task_id,
        tools_used=toolbox.used,
        latency_ms=latency_ms,
        llm_provider=get_llm().name,
        stages=stages,
        channel=channel,
    )
    await hub.broadcast(
        "conversation",
        {
            "conversation_id": conversation.id,
            "elder_id": elder.id,
            "intent": detected_intent,
            "question": text,
            "answer": reply_text,
            "audio_url": audio_url,
            "tools_used": toolbox.used,
            "latency_ms": latency_ms,
            "decision": decision_payload,
            "channel": channel,
        },
        elder_id=elder.id,
    )
    logger.info(
        "对话[%s/%s] %s -> 意图:%s 用时:%dms", channel, elder.name, text, detected_intent, latency_ms
    )
    yield {"event": "final", "reply": reply}


async def handle_utterance(
    db: AsyncSession,
    elder: Elder,
    text: str,
    device_id: str = "",
    conversation_id: int | None = None,
    channel: str = CHANNEL_DOG,
) -> AgentReply:
    """非流式入口：走完整个流程，只要最后的回复。"""
    reply: AgentReply | None = None
    async for event in handle_utterance_events(db, elder, text, device_id, conversation_id, channel):
        if event["event"] == "final":
            reply = event["reply"]
    assert reply is not None
    return reply
