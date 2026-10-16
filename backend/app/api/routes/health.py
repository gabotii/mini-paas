from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.redis import get_async_redis
from app.db.session import get_async_engine
from app.services.heartbeats import HEARTBEAT_KEYS

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    return {"status": "ok"}


async def _check_database() -> None:
    async with get_async_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_redis() -> None:
    await get_async_redis().ping()


async def _timed(check: Any) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        await asyncio.wait_for(check(), timeout=3)
    except Exception as exc:  # noqa: BLE001 — any failure means "not ready"
        return {"status": "error", "error": type(exc).__name__}
    return {"status": "ok", "latency_ms": round((time.perf_counter() - started) * 1000, 1)}


@router.get("/ready", summary="Readiness probe (PostgreSQL, Redis, background services)")
async def ready() -> JSONResponse:
    database, redis_check = await asyncio.gather(_timed(_check_database), _timed(_check_redis))
    checks: dict[str, Any] = {"database": database, "redis": redis_check}

    # Workers/agent are reported but do not gate readiness: the API can still serve reads.
    if redis_check["status"] == "ok":
        try:
            values = await get_async_redis().mget(list(HEARTBEAT_KEYS.values()))
            for name, value in zip(HEARTBEAT_KEYS, values, strict=True):
                checks[name] = {"status": "ok" if value else "missing"}
        except Exception as exc:  # noqa: BLE001 — heartbeats are informational only
            checks["background"] = {"status": "unknown", "error": type(exc).__name__}

    critical_ok = database["status"] == "ok" and redis_check["status"] == "ok"
    return JSONResponse(
        {"status": "ready" if critical_ok else "not_ready", "checks": checks},
        status_code=200 if critical_ok else 503,
    )
