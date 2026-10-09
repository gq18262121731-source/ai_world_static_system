from datetime import datetime

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, User


async def record(
    db: AsyncSession,
    user: User | None,
    action: str,
    target_type: str = "",
    target_id: str | int = "",
    detail: dict | None = None,
    request: Request | None = None,
) -> AuditLog:
    log = AuditLog(
        user_id=user.id if user else None,
        username=user.username if user else "system",
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        detail=detail or {},
        ip=request.client.host if request and request.client else "",
        created_at=datetime.now(),
    )
    db.add(log)
    await db.flush()
    return log


async def list_logs(
    db: AsyncSession, limit: int = 100, offset: int = 0
) -> list[AuditLog]:
    result = await db.execute(
        select(AuditLog).order_by(AuditLog.id.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all())
