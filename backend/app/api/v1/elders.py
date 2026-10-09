from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.core.deps import AdminUser, CurrentUser, DbSession, accessible_elder_ids, get_accessible_elder
from app.models import Elder, EmergencyContact
from app.schemas.common import Msg
from app.schemas.elder import (
    ElderCreate,
    ElderOut,
    ElderUpdate,
    EmergencyContactBase,
    EmergencyContactOut,
)
from app.services import audit_service

router = APIRouter(prefix="/elders", tags=["老人档案"])


def _to_out(elder: Elder) -> ElderOut:
    data = ElderOut.model_validate(elder)
    data.age = elder.age
    return data


@router.get("", response_model=list[ElderOut], summary="老人列表")
async def list_elders(db: DbSession, user: CurrentUser):
    allowed = await accessible_elder_ids(db, user)
    stmt = select(Elder).order_by(Elder.id)
    if allowed is not None:
        stmt = stmt.where(Elder.id.in_(allowed or [-1]))
    result = await db.execute(stmt)
    return [_to_out(e) for e in result.scalars().unique().all()]


@router.get("/{elder_id}", response_model=ElderOut, summary="老人详情")
async def get_elder(elder_id: int, db: DbSession, user: CurrentUser):
    elder = await get_accessible_elder(db, user, elder_id)
    return _to_out(elder)


@router.post("", response_model=ElderOut, summary="新建老人档案")
async def create_elder(
    payload: ElderCreate, db: DbSession, operator: AdminUser, request: Request
):
    elder = Elder(**payload.model_dump(exclude={"emergency_contacts"}))
    db.add(elder)
    await db.flush()
    for contact in payload.emergency_contacts:
        db.add(EmergencyContact(elder_id=elder.id, **contact.model_dump()))
    await audit_service.record(
        db, operator, "create_elder", "elder", elder.id, {"name": elder.name}, request
    )
    await db.commit()
    await db.refresh(elder)
    return _to_out(elder)


@router.put("/{elder_id}", response_model=ElderOut, summary="更新老人档案")
async def update_elder(
    elder_id: int,
    payload: ElderUpdate,
    db: DbSession,
    operator: AdminUser,
    request: Request,
):
    elder = await db.get(Elder, elder_id)
    if not elder:
        raise HTTPException(status_code=404, detail="老人档案不存在")
    # exclude_unset 让"显式清空某个字段"成为可能，未传的字段保持不变
    changes = payload.model_dump(exclude_unset=True)
    audit_changes = payload.model_dump(mode="json", exclude_unset=True)
    for key, value in changes.items():
        setattr(elder, key, value)
    await audit_service.record(
        db, operator, "update_elder", "elder", elder.id, audit_changes, request
    )
    await db.commit()
    await db.refresh(elder)
    return _to_out(elder)


@router.delete("/{elder_id}", response_model=Msg, summary="删除老人档案")
async def delete_elder(
    elder_id: int, db: DbSession, operator: AdminUser, request: Request
):
    elder = await db.get(Elder, elder_id)
    if not elder:
        raise HTTPException(status_code=404, detail="老人档案不存在")
    await db.delete(elder)
    await audit_service.record(
        db, operator, "delete_elder", "elder", elder_id, request=request
    )
    await db.commit()
    return Msg(message="已删除")


@router.post(
    "/{elder_id}/contacts",
    response_model=EmergencyContactOut,
    summary="新增紧急联系人",
)
async def add_contact(
    elder_id: int,
    payload: EmergencyContactBase,
    db: DbSession,
    operator: AdminUser,
    request: Request,
):
    elder = await db.get(Elder, elder_id)
    if not elder:
        raise HTTPException(status_code=404, detail="老人档案不存在")
    contact = EmergencyContact(elder_id=elder_id, **payload.model_dump())
    db.add(contact)
    await audit_service.record(
        db, operator, "add_contact", "elder", elder_id, payload.model_dump(), request
    )
    await db.commit()
    await db.refresh(contact)
    return EmergencyContactOut.model_validate(contact)


@router.delete("/{elder_id}/contacts/{contact_id}", response_model=Msg, summary="删除紧急联系人")
async def delete_contact(
    elder_id: int, contact_id: int, db: DbSession, _: AdminUser
):
    contact = await db.get(EmergencyContact, contact_id)
    if not contact or contact.elder_id != elder_id:
        raise HTTPException(status_code=404, detail="联系人不存在")
    await db.delete(contact)
    await db.commit()
    return Msg(message="已删除")
