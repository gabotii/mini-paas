"""Real-time event contracts published on Redis pub/sub and relayed to WebSocket clients.

Channels
  logs:deployment:{deployment_id}  — log lines of one deployment (build, deploy, runtime)
  events:user:{user_id}            — status changes and metric samples for a user's projects

Publishing is best-effort: if Redis is unavailable the data is still persisted in PostgreSQL
and clients catch up from the REST endpoints.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import redis

from app.core.logging import get_logger

log = get_logger(__name__)


def logs_channel(deployment_id: uuid.UUID | str) -> str:
    return f"logs:deployment:{deployment_id}"


def user_channel(user_id: uuid.UUID | str) -> str:
    return f"events:user:{user_id}"


def encode(event_type: str, data: dict[str, Any]) -> str:
    return json.dumps({"type": event_type, "data": data}, default=str, separators=(",", ":"))


def publish(client: redis.Redis, channel: str, event_type: str, data: dict[str, Any]) -> None:
    try:
        client.publish(channel, encode(event_type, data))
    except redis.RedisError as exc:
        log.warning("event_publish_failed", channel=channel, error=str(exc))
