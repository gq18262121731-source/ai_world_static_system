import asyncio
import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.events import hub
from app.core.logging import get_logger
from app.core.security import decode_access_token
from app.models import FamilyLink, User
from app.models.enums import UserRole

router = APIRouter(tags=["实时推送"])
logger = get_logger(__name__)


async def _allowed_elder_ids(token: str) -> set[int] | None:
    """返回该连接可接收的老人范围, None 表示全部。"""
    payload = decode_access_token(token)
    if not payload:
        return set()
    async with SessionLocal() as db:
        user = await db.get(User, int(payload["sub"]))
        if not user or not user.is_active:
            return set()
        if user.role in (UserRole.ADMIN, UserRole.STAFF):
            return None
        result = await db.execute(
            select(FamilyLink.elder_id).where(FamilyLink.user_id == user.id)
        )
        return {row[0] for row in result.all()}


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query("")):
    elder_ids = await _allowed_elder_ids(token)
    if elder_ids is not None and not elder_ids:
        await websocket.close(code=4401)
        return

    await hub.connect(websocket, elder_ids)
    try:
        # 用 default=str 兜底，避免历史事件里出现无法序列化的值时整条连接被断开
        await websocket.send_text(
            json.dumps(
                {"event": "connected", "data": {"recent": hub.recent(20)}},
                ensure_ascii=False,
                default=str,
            )
        )
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                if message == "ping":
                    await websocket.send_text("pong")
            except TimeoutError:
                await websocket.send_json({"event": "heartbeat", "data": {}})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("WebSocket 异常关闭: %s", exc)
    finally:
        await hub.disconnect(websocket)


