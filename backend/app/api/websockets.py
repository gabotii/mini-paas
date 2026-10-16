"""WebSocket endpoints: live deployment logs and per-user events.

Authentication uses single-use tickets (`POST /api/auth/ws-ticket`). Each connection:
subscribe to the Redis channel *first*, then send the persisted backlog, then forward live
messages — with ids used to drop lines already contained in the backlog.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from redis.exceptions import RedisError
from starlette.websockets import WebSocketState

from app.core.logging import get_logger
from app.core.redis import get_async_redis
from app.db.session import get_async_sessionmaker
from app.deployments import events
from app.repositories.deployments import DeploymentRepository
from app.repositories.observability import LogRepository
from app.schemas.observability import LogLine
from app.services.ws_tickets import WebSocketTickets

log = get_logger(__name__)
router = APIRouter()

BACKLOG_LINES = 1000
KEEPALIVE_SECONDS = 25
CLOSE_UNAUTHORIZED = 4401
CLOSE_NOT_FOUND = 4404
CLOSE_GOING_AWAY = 1001


class ConnectionRegistry:
    """Tracks open sockets so shutdown can close them cleanly (close code 1001)."""

    def __init__(self) -> None:
        self._sockets: set[WebSocket] = set()

    @property
    def count(self) -> int:
        return len(self._sockets)

    def add(self, ws: WebSocket) -> None:
        self._sockets.add(ws)

    def discard(self, ws: WebSocket) -> None:
        self._sockets.discard(ws)

    async def close_all(self) -> None:
        for ws in list(self._sockets):
            with contextlib.suppress(Exception):
                await ws.close(code=CLOSE_GOING_AWAY)
        self._sockets.clear()


connection_registry = ConnectionRegistry()


async def _authenticate(ws: WebSocket) -> uuid.UUID | None:
    ticket = ws.query_params.get("ticket", "")
    try:
        return await WebSocketTickets(get_async_redis()).redeem(ticket)
    except RedisError:
        return None


async def _send(ws: WebSocket, event_type: str, data: Any) -> None:
    await ws.send_text(json.dumps({"type": event_type, "data": data}, default=str))


async def _pump(ws: WebSocket, channel: str, on_message: Any) -> None:
    """Forward pub/sub messages until the client disconnects."""
    pubsub = get_async_redis().pubsub(ignore_subscribe_messages=True)
    await pubsub.subscribe(channel)
    connection_registry.add(ws)
    try:
        ready = await on_message(None)  # caller sends its backlog after subscribing
        if ready is False:
            return

        async def reader() -> None:
            last_ping = asyncio.get_running_loop().time()
            while True:
                message = await pubsub.get_message(timeout=1.0)
                if message is not None:
                    await on_message(message["data"])
                now = asyncio.get_running_loop().time()
                if now - last_ping > KEEPALIVE_SECONDS:
                    await _send(ws, "ping", {})
                    last_ping = now

        async def receiver() -> None:
            while True:
                await ws.receive_text()  # clients send nothing meaningful; detects disconnects

        tasks = [asyncio.create_task(reader()), asyncio.create_task(receiver())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect | RuntimeError):
                log.warning("websocket_error", error=type(exc).__name__)
    finally:
        connection_registry.discard(ws)
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()  # type: ignore[no-untyped-call]
        if ws.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await ws.close()


@router.websocket("/ws/deployments/{deployment_id}/logs")
async def deployment_logs_ws(ws: WebSocket, deployment_id: uuid.UUID) -> None:
    user_id = await _authenticate(ws)
    if user_id is None:
        await ws.close(code=CLOSE_UNAUTHORIZED)
        return
    async with get_async_sessionmaker()() as session:
        deployment = await DeploymentRepository(session).get_for_user(deployment_id, user_id)
    if deployment is None:
        await ws.close(code=CLOSE_NOT_FOUND)
        return
    await ws.accept()
    state = {"last_id": 0}

    async def on_message(raw: str | None) -> bool | None:
        if raw is None:
            async with get_async_sessionmaker()() as session:
                rows = await LogRepository(session).list(deployment_id, limit=BACKLOG_LINES, tail=True)
            lines = [LogLine.model_validate(r).model_dump(mode="json") for r in rows]
            if lines:
                state["last_id"] = lines[-1]["id"]
            await _send(ws, "backlog", {"lines": lines})
            return True
        payload = json.loads(raw)
        lines = [ln for ln in payload.get("data", {}).get("lines", []) if ln["id"] > state["last_id"]]
        if lines:
            state["last_id"] = lines[-1]["id"]
            await _send(ws, "logs", {"lines": lines})
        return None

    with contextlib.suppress(WebSocketDisconnect, RedisError):
        await _pump(ws, events.logs_channel(deployment_id), on_message)


@router.websocket("/ws/events")
async def user_events_ws(ws: WebSocket) -> None:
    user_id = await _authenticate(ws)
    if user_id is None:
        await ws.close(code=CLOSE_UNAUTHORIZED)
        return
    await ws.accept()

    async def on_message(raw: str | None) -> bool | None:
        if raw is None:
            await _send(ws, "hello", {"user_id": str(user_id)})
            return True
        await ws.send_text(raw)
        return None

    with contextlib.suppress(WebSocketDisconnect, RedisError):
        await _pump(ws, events.user_channel(user_id), on_message)
