from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import decode_access_token
from app.models import Elder, FamilyLink, User
from app.models.enums import UserRole

bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    request: Request,
    db: DbSession,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
) -> User:
    token = credentials.credentials if credentials else request.query_params.get("token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="未提供登录凭证"
        )
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期，请重新登录"
        )
    user = await db.get(User, int(payload["sub"]))
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不可用")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: str):
    async def _checker(user: CurrentUser) -> User:
        if roles and user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="没有操作权限"
            )
        return user

    return _checker


AdminUser = Annotated[User, Depends(require_roles(UserRole.ADMIN, UserRole.STAFF))]


async def accessible_elder_ids(db: AsyncSession, user: User) -> list[int] | None:
    """管理员/服务人员可见全部老人, 家属只能看到绑定的老人。None 表示不限制。"""
    if user.role in (UserRole.ADMIN, UserRole.STAFF):
        return None
    result = await db.execute(
        select(FamilyLink.elder_id).where(FamilyLink.user_id == user.id)
    )
    return [row[0] for row in result.all()]


async def get_accessible_elder(db: AsyncSession, user: User, elder_id: int) -> Elder:
    elder = await db.get(Elder, elder_id)
    if not elder:
        raise HTTPException(status_code=404, detail="老人档案不存在")
    allowed = await accessible_elder_ids(db, user)
    if allowed is not None and elder_id not in allowed:
        raise HTTPException(status_code=403, detail="无权访问该老人的数据")
    return elder
