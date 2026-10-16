"""Single-use, short-lived tickets for authenticating WebSocket connections.

Browsers cannot set Authorization headers on WebSocket handshakes. Instead of putting the
JWT in the URL (where it ends up in proxy logs), the client exchanges its access token for an
opaque ticket valid for 30 seconds and usable once.
"""

from __future__ import annotations

import uuid

import redis.asyncio as aioredis

from app.core.security import generate_opaque_token, hash_token

TICKET_TTL_SECONDS = 30


class WebSocketTickets:
    def __init__(self, client: aioredis.Redis) -> None:
        self._redis = client

    async def issue(self, user_id: uuid.UUID) -> str:
        ticket = generate_opaque_token()
        await self._redis.set(f"ws-ticket:{hash_token(ticket)}", str(user_id), ex=TICKET_TTL_SECONDS)
        return ticket

    async def redeem(self, ticket: str) -> uuid.UUID | None:
        if not ticket or len(ticket) > 128:
            return None
        key = f"ws-ticket:{hash_token(ticket)}"
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.get(key)
            pipe.delete(key)
            value, _ = await pipe.execute()
        if not value:
            return None
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
