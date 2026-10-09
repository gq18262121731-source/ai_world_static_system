import json
import time
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class _MemoryCache:
    """Redis 不可用时的进程内退化实现, 保证开发环境零依赖可跑。"""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float | None, str]] = {}

    def _alive(self, key: str) -> bool:
        item = self._data.get(key)
        if item is None:
            return False
        expire_at, _ = item
        if expire_at is not None and expire_at < time.time():
            self._data.pop(key, None)
            return False
        return True

    async def get(self, key: str) -> str | None:
        return self._data[key][1] if self._alive(key) else None

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._data[key] = (time.time() + ex if ex else None, value)

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)


class Cache:
    """统一缓存入口: 优先 Redis, 连接失败自动退化为内存缓存。"""

    def __init__(self) -> None:
        self._redis = None
        self._memory = _MemoryCache()
        self.backend = "memory"

    async def connect(self) -> None:
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(
                settings.redis_url, encoding="utf-8", decode_responses=True
            )
            await client.ping()
            self._redis = client
            self.backend = "redis"
            logger.info("Redis 已连接: %s", settings.redis_url)
        except Exception as exc:
            self._redis = None
            self.backend = "memory"
            logger.warning("Redis 不可用(%s), 已退化为进程内缓存", exc)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def get_json(self, key: str) -> Any | None:
        raw = await (self._redis.get(key) if self._redis else self._memory.get(key))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def set_json(self, key: str, value: Any, ex: int | None = 3600) -> None:
        raw = json.dumps(value, ensure_ascii=False, default=str)
        if self._redis:
            await self._redis.set(key, raw, ex=ex)
        else:
            await self._memory.set(key, raw, ex=ex)

    async def delete(self, key: str) -> None:
        if self._redis:
            await self._redis.delete(key)
        else:
            await self._memory.delete(key)


cache = Cache()
