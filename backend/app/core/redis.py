from __future__ import annotations

from functools import lru_cache

import redis
import redis.asyncio as aioredis

from app.core.config import get_settings


@lru_cache
def get_async_redis() -> aioredis.Redis:
    client: aioredis.Redis = aioredis.Redis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=10,
        health_check_interval=30,
    )
    return client


@lru_cache
def get_sync_redis() -> redis.Redis:
    return redis.Redis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=10,
        health_check_interval=30,
    )
