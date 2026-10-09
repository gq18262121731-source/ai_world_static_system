"""智能体对话的检索与追踪。

把「老人一句话 + 机器狗一句回复」还原成一轮完整对话(turn),
并带上意图、工具调用链、各阶段耗时与外出决策，供后台复核与调参。
"""

from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Elder, Message
from app.models.enums import MessageRole


def _apply_filters(
    stmt: Select,
    elder_ids: list[int] | None,
    elder_id: int | None,
    intent: str | None,
    keyword: str | None,
    start: datetime | None,
    end: datetime | None,
) -> Select:
    if elder_ids is not None:
        stmt = stmt.where(Conversation.elder_id.in_(elder_ids or [-1]))
    if elder_id is not None:
        stmt = stmt.where(Conversation.elder_id == elder_id)
    if intent:
        stmt = stmt.where(Message.intent == intent)
    if keyword:
        stmt = stmt.where(Message.text.ilike(f"%{keyword}%"))
    if start:
        stmt = stmt.where(Message.created_at >= start)
    if end:
        stmt = stmt.where(Message.created_at <= end)
    return stmt


async def list_turns(
    db: AsyncSession,
    elder_ids: list[int] | None = None,
    elder_id: int | None = None,
    intent: str | None = None,
    keyword: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """系统回复承载了意图与工具链, 以它为主体, 再配对上一条老人发言。"""
    base = (
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.role == MessageRole.SYSTEM)
    )
    base = _apply_filters(base, elder_ids, elder_id, intent, keyword, start, end)

    count_stmt = (
        select(func.count(Message.id))
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.role == MessageRole.SYSTEM)
    )
    count_stmt = _apply_filters(
        count_stmt, elder_ids, elder_id, intent, keyword, start, end
    )
    total = int((await db.execute(count_stmt)).scalar() or 0)

    replies = list(
        (
            await db.execute(base.order_by(Message.id.desc()).limit(limit).offset(offset))
        ).scalars().all()
    )
    if not replies:
        return [], total

    conversation_ids = {m.conversation_id for m in replies}
    conversations = {
        c.id: c
        for c in (
            await db.execute(
                select(Conversation).where(Conversation.id.in_(conversation_ids))
            )
        ).scalars().all()
    }
    elder_ids_in_page = {c.elder_id for c in conversations.values()}
    elders = {
        e.id: e
        for e in (
            await db.execute(select(Elder).where(Elder.id.in_(elder_ids_in_page)))
        ).scalars().unique().all()
    }

    # 一次取回相关会话的全部老人发言, 在内存里配对, 避免逐条查询
    questions = list(
        (
            await db.execute(
                select(Message)
                .where(
                    Message.conversation_id.in_(conversation_ids),
                    Message.role == MessageRole.ELDER,
                    Message.id < max(m.id for m in replies),
                )
                .order_by(Message.id)
            )
        ).scalars().all()
    )
    by_conversation: dict[int, list[Message]] = {}
    for item in questions:
        by_conversation.setdefault(item.conversation_id, []).append(item)

    turns = []
    for reply in replies:
        candidates = [
            m for m in by_conversation.get(reply.conversation_id, []) if m.id < reply.id
        ]
        question = candidates[-1] if candidates else None
        conversation = conversations.get(reply.conversation_id)
        elder = elders.get(conversation.elder_id) if conversation else None
        meta = reply.meta or {}
        turns.append(
            {
                "message_id": reply.id,
                "conversation_id": reply.conversation_id,
                "elder_id": conversation.elder_id if conversation else None,
                "elder_name": elder.name if elder else "",
                "device_id": conversation.device_id if conversation else "",
                "intent": reply.intent,
                "intent_confidence": meta.get("intent_confidence"),
                "question": question.text if question else "",
                "answer": reply.text,
                "audio_url": reply.audio_url,
                "tools": meta.get("tools", []),
                "stages": meta.get("stages", {}),
                "llm_provider": meta.get("llm_provider", ""),
                "decision": meta.get("decision"),
                "task_id": meta.get("task_id"),
                "latency_ms": reply.latency_ms,
                "created_at": reply.created_at,
            }
        )
    return turns, total


async def intent_distribution(
    db: AsyncSession, elder_ids: list[int] | None = None
) -> dict[str, int]:
    stmt = (
        select(Message.intent, func.count(Message.id))
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.role == MessageRole.SYSTEM, Message.intent != "")
        .group_by(Message.intent)
    )
    if elder_ids is not None:
        stmt = stmt.where(Conversation.elder_id.in_(elder_ids or [-1]))
    return {row[0]: row[1] for row in (await db.execute(stmt)).all()}


async def latency_stats(
    db: AsyncSession, elder_ids: list[int] | None = None
) -> dict[str, float]:
    stmt = (
        select(
            func.count(Message.id),
            func.avg(Message.latency_ms),
            func.max(Message.latency_ms),
        )
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.role == MessageRole.SYSTEM)
    )
    if elder_ids is not None:
        stmt = stmt.where(Conversation.elder_id.in_(elder_ids or [-1]))
    count, avg, peak = (await db.execute(stmt)).one()
    return {
        "total_turns": int(count or 0),
        "avg_latency_ms": round(float(avg or 0), 1),
        "max_latency_ms": int(peak or 0),
    }
