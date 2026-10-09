import asyncio
import json
from datetime import datetime
from typing import Any

from fastapi import WebSocket

from app.core.logging import get_logger

logger = get_logger(__name__)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class WebSocketHub:
    """按 elder 维度维护前端订阅, 用于实时推送告警/轨迹/对话。"""

    def __init__(self) -> None:
        self._clients: dict[WebSocket, set[int]] = {}
        self._lock = asyncio.Lock()
        self._recent: list[dict] = []

    async def connect(self, ws: WebSocket, elder_ids: set[int] | None = None) -> None:
        await ws.accept()
        async with self._lock:
            self._clients[ws] = elder_ids or set()
        logger.info("WebSocket 已连接, 当前在线 %d", len(self._clients))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.pop(ws, None)

    async def subscribe(self, ws: WebSocket, elder_ids: set[int]) -> None:
        async with self._lock:
            if ws in self._clients:
                self._clients[ws] = elder_ids

    async def broadcast(
        self, event: str, data: dict, elder_id: int | None = None
    ) -> None:
        message = {
            "event": event,
            "elder_id": elder_id,
            "data": data,
            "ts": datetime.now().isoformat(),
        }
        payload = json.dumps(message, ensure_ascii=False, default=_json_default)

        # 缓存已归一化的副本: data 里可能带 datetime 等非 JSON 原生类型,
        # 新连接建立时要把这批历史事件直接发出去, 必须保证可序列化
        self._recent.append(json.loads(payload))
        if len(self._recent) > 200:
            self._recent = self._recent[-200:]

        async with self._lock:
            targets = [
                ws
                for ws, ids in self._clients.items()
                if not ids or elder_id is None or elder_id in ids
            ]
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.pop(ws, None)

    def recent(self, limit: int = 50) -> list[dict]:
        return self._recent[-limit:]

    @property
    def online(self) -> int:
        return len(self._clients)


hub = WebSocketHub()
