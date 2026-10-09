from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Elder, RuleConfig
from app.rules.definitions import CHRONIC_TIGHTEN, DEFAULT_RULES, deep_merge


async def _db_overrides(db: AsyncSession, elder_id: int | None) -> dict:
    stmt = select(RuleConfig).where(RuleConfig.enabled.is_(True))
    if elder_id is None:
        stmt = stmt.where(RuleConfig.elder_id.is_(None))
    else:
        stmt = stmt.where(
            (RuleConfig.elder_id.is_(None)) | (RuleConfig.elder_id == elder_id)
        )
    rows = list((await db.execute(stmt)).scalars().all())
    # 全局在前, 个性化在后, 保证个性化覆盖全局
    rows.sort(key=lambda r: (r.elder_id is not None, r.id))
    merged: dict = {}
    for row in rows:
        merged = deep_merge(merged, {row.rule_key: row.value or {}})
    return merged


async def get_effective_rules(
    db: AsyncSession, elder: Elder | None = None
) -> tuple[dict, list[str]]:
    """合成生效阈值: 默认值 -> 全局配置 -> 老人个性化配置 -> 慢病自动收紧。

    返回 (阈值字典, 被覆盖的规则键列表)。
    """
    rules = deepcopy(DEFAULT_RULES)
    elder_id = elder.id if elder else None
    overrides = await _db_overrides(db, elder_id)
    overridden_keys = sorted(overrides.keys())
    rules = deep_merge(rules, overrides)

    if elder:
        for disease in elder.chronic_diseases or []:
            tighten = CHRONIC_TIGHTEN.get(str(disease).strip())
            if tighten:
                rules = deep_merge(rules, tighten)
                overridden_keys.extend(
                    k for k in tighten if k not in overridden_keys
                )
    return rules, overridden_keys


async def get_thresholds_for(db: AsyncSession, elder: Elder | None) -> dict:
    rules, _ = await get_effective_rules(db, elder)
    return rules
