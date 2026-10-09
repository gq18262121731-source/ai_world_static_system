from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.core.deps import AdminUser, CurrentUser, DbSession
from app.core.security import create_access_token, hash_password, verify_password
from app.models import FamilyLink, User
from app.schemas.auth import LoginRequest, TokenResponse, UserCreate, UserOut
from app.services import audit_service

router = APIRouter(prefix="/auth", tags=["认证"])


def _to_user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        real_name=user.real_name,
        phone=user.phone,
        role=user.role,
        is_active=user.is_active,
        elder_ids=[link.elder_id for link in user.family_links],
    )


@router.post("/login", response_model=TokenResponse, summary="登录")
async def login(payload: LoginRequest, db: DbSession, request: Request):
    result = await db.execute(select(User).where(User.username == payload.username))
    user = result.scalars().first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码不正确")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被停用")
    await audit_service.record(db, user, "login", "user", user.id, request=request)
    await db.commit()
    token = create_access_token(str(user.id), user.role)
    return TokenResponse(access_token=token, user=_to_user_out(user))


@router.get("/me", response_model=UserOut, summary="当前登录用户")
async def me(user: CurrentUser):
    return _to_user_out(user)


@router.get("/users", response_model=list[UserOut], summary="用户列表")
async def list_users(db: DbSession, _: AdminUser):
    result = await db.execute(select(User).order_by(User.id))
    return [_to_user_out(u) for u in result.scalars().unique().all()]


@router.post("/users", response_model=UserOut, summary="创建用户")
async def create_user(payload: UserCreate, db: DbSession, operator: AdminUser, request: Request):
    exists = await db.execute(select(User).where(User.username == payload.username))
    if exists.scalars().first():
        raise HTTPException(status_code=400, detail="用户名已存在")
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        real_name=payload.real_name,
        phone=payload.phone,
        role=payload.role,
    )
    db.add(user)
    await db.flush()
    for elder_id in payload.elder_ids:
        db.add(
            FamilyLink(
                user_id=user.id,
                elder_id=elder_id,
                relation=payload.relation,
                is_primary=elder_id == payload.elder_ids[0],
            )
        )
    await audit_service.record(
        db, operator, "create_user", "user", user.id, {"role": payload.role}, request
    )
    await db.commit()
    await db.refresh(user)
    return _to_user_out(user)
