"""设备主动探活。

心跳判定只能说明设备最近上报过，说明不了它现在还能不能收指令。
自检时给机器狗下发一条 ping，在超时窗口内等它回 pong，才算真正双向可达。
"""

import asyncio
import uuid

from app.core.logging import get_logger

logger = get_logger(__name__)

# nonce -> 等待中的 Future
_waiters: dict[str, asyncio.Future] = {}


def new_nonce() -> str:
    return uuid.uuid4().hex[:12]


def register(nonce: str) -> asyncio.Future:
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    _waiters[nonce] = future
    return future


def resolve(nonce: str, payload: dict) -> bool:
    """设备回了 pong，唤醒对应的等待者。"""
    future = _waiters.pop(nonce, None)
    if future is None or future.done():
        return False
    future.set_result(payload)
    return True


def discard(nonce: str) -> None:
    _waiters.pop(nonce, None)


async def wait_for_pong(nonce: str, timeout: float) -> dict | None:
    future = register(nonce)
    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except TimeoutError:
        return None
    finally:
        discard(nonce)
