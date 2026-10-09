from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.core.deps import AdminUser, CurrentUser, DbSession
from app.models import Elder, RuleConfig
from app.rules.config_store import get_effective_rules
from app.rules.definitions import DEFAULT_RULES, FIELD_LABELS, RULE_TITLES
from app.schemas.common import Msg
from app.schemas.rule import EffectiveRules, RuleConfigOut, RuleConfigUpsert
from app.services import audit_service

router = APIRouter(prefix="/rules", tags=["规则配置"])


@router.get("/defaults", summary="默认阈值与说明")
async def defaults(_: CurrentUser):
    return {
        "rules": DEFAULT_RULES,
        "titles": RULE_TITLES,
        "field_labels": FIELD_LABELS,
    }


@router.get("/effective", response_model=EffectiveRules, summary="生效阈值(含个性化覆盖)")
async def effective(db: DbSession, _: CurrentUser, elder_id: int | None = None):
    elder = await db.get(Elder, elder_id) if elder_id else None
    if elder_id and not elder:
        raise HTTPException(status_code=404, detail="老人档案不存在")
    values, overridden = await get_effective_rules(db, elder)
    return EffectiveRules(elder_id=elder_id, values=values, overridden_keys=overridden)


@router.get("/configs", response_model=list[RuleConfigOut], summary="阈值覆盖配置列表")
async def list_configs(db: DbSession, _: CurrentUser, elder_id: int | None = None):
    stmt = select(RuleConfig).order_by(RuleConfig.id)
    if elder_id is None:
        stmt = stmt.where(RuleConfig.elder_id.is_(None))
    else:
        stmt = stmt.where(RuleConfig.elder_id == elder_id)
    result = await db.execute(stmt)
    return [RuleConfigOut.model_validate(r) for r in result.scalars().all()]


@router.put("/configs", response_model=RuleConfigOut, summary="新增或更新阈值覆盖")
async def upsert_config(
    payload: RuleConfigUpsert,
    db: DbSession,
    operator: AdminUser,
    request: Request,
    elder_id: int | None = None,
):
    if payload.rule_key not in DEFAULT_RULES:
        raise HTTPException(status_code=400, detail="未知的规则项")
    stmt = select(RuleConfig).where(RuleConfig.rule_key == payload.rule_key)
    stmt = (
        stmt.where(RuleConfig.elder_id.is_(None))
        if elder_id is None
        else stmt.where(RuleConfig.elder_id == elder_id)
    )
    config = (await db.execute(stmt)).scalars().first()
    if config is None:
        config = RuleConfig(elder_id=elder_id, rule_key=payload.rule_key)
        db.add(config)
    config.value = payload.value
    config.enabled = payload.enabled
    config.description = payload.description or RULE_TITLES.get(payload.rule_key, "")
    await db.flush()
    await audit_service.record(
        db,
        operator,
        "upsert_rule",
        "rule",
        payload.rule_key,
        {"elder_id": elder_id, "value": payload.value},
        request,
    )
    await db.commit()
    await db.refresh(config)
    return RuleConfigOut.model_validate(config)


@router.delete("/configs/{config_id}", response_model=Msg, summary="删除阈值覆盖")
async def delete_config(config_id: int, db: DbSession, _: AdminUser):
    config = await db.get(RuleConfig, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    await db.delete(config)
    await db.commit()
    return Msg(message="已删除，恢复为上一级默认值")
