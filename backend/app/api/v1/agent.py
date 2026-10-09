import json
import time
from datetime import datetime

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.agent import orchestrator
from app.ai.base import ChatMessage, ChatRequest
from app.ai.factory import ai_status, get_asr, get_llm, get_tts
from app.core.db import session_scope
from app.core.deps import (
    AdminUser,
    CurrentUser,
    DbSession,
    accessible_elder_ids,
    get_accessible_elder,
)
from app.core.logging import get_logger
from app.models import Conversation, Elder
from app.models.enums import IntentType
from app.schemas.agent import (
    AgentMetrics,
    AgentReply,
    AgentTurnPage,
    ConsoleChatIn,
    ConversationOut,
    ProviderTestResult,
    PromptTemplateOut,
    PromptTemplateUpdate,
    VoiceTextIn,
)
from app.services import agent_query_service, audit_service, device_service, prompt_service

router = APIRouter(prefix="/agent", tags=["语音智能体"])
logger = get_logger(__name__)


def _sse(event: dict) -> str:
    payload = dict(event)
    reply = payload.get("reply")
    if reply is not None and hasattr(reply, "model_dump"):
        payload["reply"] = reply.model_dump(mode="json")
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


async def _stream_events(elder_id: int, text: str, device_id: str, conversation_id: int | None, channel: str):
    """流式响应期间请求级的数据库会话已经结束，这里自己开一个并在结尾提交。"""
    async with session_scope() as db:
        elder = await db.get(Elder, elder_id)
        if elder is None:
            yield _sse({"event": "error", "detail": "老人不存在"})
            return
        try:
            async for event in orchestrator.handle_utterance_events(
                db, elder, text, device_id, conversation_id, channel
            ):
                yield _sse(event)
        except Exception as exc:  # noqa: BLE001
            logger.exception("流式对话失败")
            yield _sse({"event": "error", "detail": str(exc)[:200]})


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.post("/talk/stream", summary="机器狗对话（流式：阶段事件 + 逐段回复 + 最终结果）")
async def talk_stream(payload: VoiceTextIn, db: DbSession, user: CurrentUser):
    elder_id = payload.elder_id
    if elder_id is None and payload.device_id:
        elder_id = await device_service.resolve_elder_id(db, payload.device_id)
    if elder_id is None:
        raise HTTPException(status_code=400, detail="需要指定 elder_id 或已绑定老人的 device_id")
    await get_accessible_elder(db, user, elder_id)
    return StreamingResponse(
        _stream_events(elder_id, payload.text, payload.device_id, payload.conversation_id, orchestrator.CHANNEL_DOG),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/chat/stream", summary="控制台问答（流式，只回答不播报、不动设备）")
async def chat_stream(payload: ConsoleChatIn, db: DbSession, user: CurrentUser):
    await get_accessible_elder(db, user, payload.elder_id)
    return StreamingResponse(
        _stream_events(payload.elder_id, payload.question, "", payload.conversation_id, orchestrator.CHANNEL_CONSOLE),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/chat", response_model=AgentReply, summary="控制台问答（非流式）")
async def chat(payload: ConsoleChatIn, db: DbSession, user: CurrentUser):
    elder = await get_accessible_elder(db, user, payload.elder_id)
    reply = await orchestrator.handle_utterance(
        db, elder, payload.question, "", payload.conversation_id, orchestrator.CHANNEL_CONSOLE
    )
    await db.commit()
    return reply


@router.post("/talk", response_model=AgentReply, summary="文本对话(机器狗语音识别结果入口)")
async def talk(payload: VoiceTextIn, db: DbSession, user: CurrentUser):
    elder_id = payload.elder_id
    if elder_id is None and payload.device_id:
        elder_id = await device_service.resolve_elder_id(db, payload.device_id)
    if elder_id is None:
        raise HTTPException(status_code=400, detail="需要指定 elder_id 或已绑定老人的 device_id")
    elder = await get_accessible_elder(db, user, elder_id)
    reply = await orchestrator.handle_utterance(
        db, elder, payload.text, payload.device_id, payload.conversation_id
    )
    await db.commit()
    return reply


@router.post("/voice", response_model=AgentReply, summary="上传音频对话(ASR -> 智能体 -> TTS)")
async def voice(
    db: DbSession,
    user: CurrentUser,
    elder_id: int = Form(...),
    device_id: str = Form(""),
    audio: UploadFile = File(...),
):
    elder = await get_accessible_elder(db, user, elder_id)
    raw = await audio.read()
    text = await get_asr().transcribe(raw, audio.filename or "audio.wav")
    if not text:
        raise HTTPException(status_code=400, detail="没有识别到有效语音内容")
    reply = await orchestrator.handle_utterance(db, elder, text, device_id)
    await db.commit()
    return reply


@router.get("/conversations", response_model=list[ConversationOut], summary="对话记录")
async def conversations(
    elder_id: int, db: DbSession, user: CurrentUser, limit: int = 20
):
    await get_accessible_elder(db, user, elder_id)
    result = await db.execute(
        select(Conversation)
        .where(Conversation.elder_id == elder_id)
        .order_by(Conversation.id.desc())
        .limit(limit)
    )
    return [ConversationOut.model_validate(c) for c in result.scalars().unique().all()]


@router.get("/turns", response_model=AgentTurnPage, summary="跨老人的对话轮次检索")
async def turns(
    db: DbSession,
    user: CurrentUser,
    elder_id: int | None = None,
    intent: str | None = None,
    keyword: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
):
    allowed = await accessible_elder_ids(db, user)
    if elder_id is not None:
        await get_accessible_elder(db, user, elder_id)
    items, total = await agent_query_service.list_turns(
        db,
        elder_ids=allowed,
        elder_id=elder_id,
        intent=intent,
        keyword=keyword,
        start=start,
        end=end,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    return AgentTurnPage(items=items, total=total, page=page, page_size=page_size)


@router.get("/metrics", response_model=AgentMetrics, summary="智能体运行指标")
async def metrics(db: DbSession, user: CurrentUser):
    allowed = await accessible_elder_ids(db, user)
    stats = await agent_query_service.latency_stats(db, allowed)
    return AgentMetrics(
        **stats,
        intent_distribution=await agent_query_service.intent_distribution(db, allowed),
    )


@router.get("/intents", summary="支持的意图类型")
async def intents(_: CurrentUser):
    labels = {
        IntentType.OUTING_REQUEST: "外出申请",
        IntentType.END_OUTING: "结束外出",
        IntentType.HEALTH_QUERY: "健康问询",
        IntentType.GENERAL_QA: "通用问答",
        IntentType.SOS: "紧急求助",
        IntentType.FALL_CONFIRM: "跌倒确认",
        IntentType.CHITCHAT: "日常闲聊",
    }
    return [{"value": value, "label": label} for value, label in labels.items()]


@router.get("/status", summary="AI 能力提供方状态")
async def status(_: CurrentUser):
    return ai_status()


@router.post(
    "/providers/test",
    response_model=list[ProviderTestResult],
    summary="测试 AI 能力连通性",
)
async def test_providers(_: AdminUser):
    """真实调用一次大模型与语音合成, 用于确认密钥与网络是否可用。"""
    results: list[ProviderTestResult] = []

    llm = get_llm()
    started = time.perf_counter()
    try:
        sample = await llm.chat(
            ChatRequest(
                messages=[
                    ChatMessage("system", "你是养老陪护机器狗，用一句话回答。"),
                    ChatMessage("user", "你好，你能做什么？"),
                ],
                temperature=0.3,
                max_tokens=120,
                task="general_qa",
                context={"question": "你是谁"},
            )
        )
        results.append(
            ProviderTestResult(
                capability="llm",
                provider=llm.name,
                ok=bool(sample.strip()),
                latency_ms=int((time.perf_counter() - started) * 1000),
                sample=sample.strip()[:120],
                detail=(
                    f"已降级 {llm.fallback_count} 次"
                    if getattr(llm, "fallback_count", 0)
                    else "调用正常"
                ),
            )
        )
    except Exception as exc:
        results.append(
            ProviderTestResult(
                capability="llm",
                provider=llm.name,
                ok=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                detail=str(exc)[:200],
            )
        )

    tts = get_tts()
    started = time.perf_counter()
    try:
        audio_url = await tts.synthesize("这是一次语音合成连通性测试。")
        results.append(
            ProviderTestResult(
                capability="tts",
                provider=tts.name,
                ok=bool(audio_url),
                latency_ms=int((time.perf_counter() - started) * 1000),
                sample=audio_url,
                detail="合成正常" if audio_url else "未返回音频地址",
            )
        )
    except Exception as exc:
        results.append(
            ProviderTestResult(
                capability="tts",
                provider=tts.name,
                ok=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                detail=str(exc)[:200],
            )
        )

    results.append(
        ProviderTestResult(
            capability="asr",
            provider=get_asr().name,
            ok=True,
            detail="语音识别在上传音频时才会真正调用，此处仅显示当前提供方",
        )
    )
    return results


@router.get(
    "/templates", response_model=list[PromptTemplateOut], summary="提示词与话术模板"
)
async def templates(db: DbSession, _: CurrentUser):
    return await prompt_service.list_templates(db)


@router.put(
    "/templates/{template_key}",
    response_model=PromptTemplateOut,
    summary="修改提示词或话术",
)
async def update_template(
    template_key: str,
    payload: PromptTemplateUpdate,
    db: DbSession,
    operator: AdminUser,
    request: Request,
):
    try:
        await prompt_service.upsert(
            db,
            template_key,
            payload.content,
            payload.enabled,
            operator.real_name or operator.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit_service.record(
        db, operator, "update_prompt", "prompt", template_key, request=request
    )
    await db.commit()
    items = await prompt_service.list_templates(db)
    return next(item for item in items if item["template_key"] == template_key)


@router.delete(
    "/templates/{template_key}",
    response_model=PromptTemplateOut,
    summary="恢复默认话术",
)
async def reset_template(
    template_key: str, db: DbSession, operator: AdminUser, request: Request
):
    await prompt_service.reset(db, template_key)
    await audit_service.record(
        db, operator, "reset_prompt", "prompt", template_key, request=request
    )
    await db.commit()
    items = await prompt_service.list_templates(db)
    return next(item for item in items if item["template_key"] == template_key)


@router.post("/tts", summary="文本转语音")
async def tts(text: str, _: CurrentUser):
    return {"audio_url": await get_tts().synthesize(text), "text": text}
