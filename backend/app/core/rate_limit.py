"""Fixed-window rate limiting backed by Redis.

Rules are expressed as "<requests>/<window seconds>", e.g. "10/60".
If Redis is unavailable the limiter fails open (and logs): the platform's queue is also on
Redis, so in that situation the API is already degraded and availability is preferred.
"""

from __future__ import annotations

from dataclasses import dataclass

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.core.errors import RateLimited
from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class Rule:
    limit: int
    window: int

    @classmethod
    def parse(cls, spec: str) -> Rule:
        limit, window = spec.split("/", 1)
        return cls(limit=int(limit), window=int(window))


class RateLimiter:
    def __init__(self, client: aioredis.Redis, enabled: bool = True) -> None:
        self._redis = client
        self._enabled = enabled

    async def hit(self, scope: str, identity: str, rule: Rule) -> None:
        """Count one request; raise RateLimited when the window budget is exhausted."""
        if not self._enabled:
            return
        key = f"rl:{scope}:{identity}"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.set(key, 0, ex=rule.window, nx=True)
                pipe.incr(key)
                pipe.ttl(key)
                _, count, ttl = await pipe.execute()
        except RedisError as exc:
            log.warning("rate_limiter_unavailable", scope=scope, error=str(exc))
            return
        if int(count) > rule.limit:
            retry_after = max(int(ttl), 1)
            raise RateLimited(
                details={"retry_after_seconds": retry_after},
                headers={"Retry-After": str(retry_after)},
            )
