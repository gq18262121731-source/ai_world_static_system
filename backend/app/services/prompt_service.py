"""提示词与话术模板的读取、渲染与维护。

读取路径: 内存缓存 -> 数据库覆盖 -> 代码默认值。
任何一步取不到内容都会回落到默认值, 保证话术被改坏时智能体依然可用。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.templates import TEMPLATES, default_content
from app.core.logging import get_logger
from app.models import PromptTemplate

logger = get_logger(__name__)

# key -> content, 由增删改操作主动失效
_cache: dict[str, str] = {}
_cache_loaded = False


class _SafeDict(dict):
    """缺失的占位符原样保留, 避免话术里写错变量名导致整条链路报错。"""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def safe_format(text: str, **kwargs) -> str:
    try:
        return text.format_map(_SafeDict(**kwargs))
    except (ValueError, IndexError):
        # 话术里出现了非法的花括号写法, 原样返回好过抛异常
        return text


def invalidate_cache() -> None:
    global _cache_loaded
    _cache.clear()
    _cache_loaded = False


async def _ensure_cache(db: AsyncSession) -> None:
    global _cache_loaded
    if _cache_loaded:
        return
    rows = (
        await db.execute(
            select(PromptTemplate).where(PromptTemplate.enabled.is_(True))
        )
    ).scalars().all()
    _cache.clear()
    for row in rows:
        if row.content and row.content.strip():
            _cache[row.template_key] = row.content
    _cache_loaded = True


async def get_template(db: AsyncSession, key: str) -> str:
    await _ensure_cache(db)
    return _cache.get(key) or default_content(key)


async def render(db: AsyncSession, key: str, **kwargs) -> str:
    return safe_format(await get_template(db, key), **kwargs)


async def list_templates(db: AsyncSession) -> list[dict]:
    """返回全部模板的当前状态, 标出哪些被后台覆盖过。"""
    rows = {
        row.template_key: row
        for row in (await db.execute(select(PromptTemplate))).scalars().all()
    }
    items = []
    for key, meta in TEMPLATES.items():
        row = rows.get(key)
        overridden = bool(row and row.enabled and row.content.strip())
        items.append(
            {
                "template_key": key,
                "name": meta["name"],
                "category": meta["category"],
                "description": meta["description"],
                "placeholders": meta["placeholders"],
                "default_content": meta["content"],
                "content": row.content if overridden else meta["content"],
                "overridden": overridden,
                "enabled": row.enabled if row else True,
                "updated_by": row.updated_by if row else "",
                "updated_at": row.updated_at if row else None,
            }
        )
    return items


async def upsert(
    db: AsyncSession, key: str, content: str, enabled: bool, operator: str
) -> PromptTemplate:
    meta = TEMPLATES.get(key)
    if meta is None:
        raise ValueError(f"未知的模板项: {key}")
    row = (
        await db.execute(
            select(PromptTemplate).where(PromptTemplate.template_key == key)
        )
    ).scalars().first()
    if row is None:
        row = PromptTemplate(template_key=key)
        db.add(row)
    row.name = meta["name"]
    row.category = meta["category"]
    row.description = meta["description"]
    row.content = content
    row.enabled = enabled
    row.updated_by = operator
    await db.flush()
    invalidate_cache()
    logger.info("话术模板 %s 已由 %s 更新", key, operator)
    return row


async def reset(db: AsyncSession, key: str) -> None:
    row = (
        await db.execute(
            select(PromptTemplate).where(PromptTemplate.template_key == key)
        )
    ).scalars().first()
    if row is not None:
        await db.delete(row)
        await db.flush()
    invalidate_cache()
    logger.info("话术模板 %s 已恢复默认值", key)
