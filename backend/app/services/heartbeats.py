"""Liveness heartbeats written by background processes and reported by /ready."""

from __future__ import annotations

import redis

from app.core.logging import get_logger

log = get_logger(__name__)

HEARTBEAT_KEYS = {
    "worker": "heartbeat:worker",
    "agent": "heartbeat:agent",
}
HEARTBEAT_TTL_SECONDS = 90


def beat(client: redis.Redis, component: str) -> None:
    try:
        client.set(HEARTBEAT_KEYS[component], "1", ex=HEARTBEAT_TTL_SECONDS)
    except redis.RedisError as exc:
        log.warning("heartbeat_failed", component=component, error=str(exc))
