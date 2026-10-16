"""LogSink — batched persistence + real-time fan-out of deployment log lines.

Writers (build output, pipeline steps, the runtime log collector) call `write()`. Lines are
buffered and flushed every `flush_interval` seconds or `batch_size` lines: one INSERT for the
batch, then one pub/sub message with the inserted rows (ids included, so WebSocket clients can
de-duplicate against the backlog they fetched).

The per-deployment byte budget protects PostgreSQL: once exceeded, a single notice is written
and further lines are dropped. Replacing PostgreSQL with Loki/Elasticsearch means replacing
`_persist()` only.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import redis
from sqlalchemy import insert, update
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.deployments import events
from app.models import Deployment, DeploymentLog, LogLevel, LogSource

log = get_logger(__name__)

MAX_LINE_CHARS = 4000


@dataclass
class _Pending:
    timestamp: datetime
    level: LogLevel
    source: LogSource
    message: str


class LogSink:
    def __init__(
        self,
        session_factory: Callable[[], AbstractContextManager[Session]],
        redis_client: redis.Redis,
        deployment_id: uuid.UUID,
        *,
        max_bytes: int,
        used_bytes: int = 0,
        flush_interval: float = 0.5,
        batch_size: int = 200,
        background: bool = True,
    ) -> None:
        self._session_factory = session_factory
        self._redis = redis_client
        self.deployment_id = deployment_id
        self._max_bytes = max_bytes
        self._used = used_bytes
        self._limit_noticed = used_bytes >= max_bytes
        self._flush_interval = flush_interval
        self._batch_size = batch_size
        self._buffer: list[_Pending] = []
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._flusher: threading.Thread | None = None
        if background:
            self._flusher = threading.Thread(target=self._loop, name="log-sink", daemon=True)
            self._flusher.start()

    # ------------------------------------------------------------------ writing
    def write(
        self,
        message: str,
        *,
        level: LogLevel = LogLevel.INFO,
        source: LogSource = LogSource.SYSTEM,
        timestamp: datetime | None = None,
    ) -> None:
        message = message.rstrip("\r\n")[:MAX_LINE_CHARS]
        size = len(message.encode("utf-8", errors="replace"))
        with self._lock:
            if self._used + size > self._max_bytes:
                if not self._limit_noticed:
                    self._limit_noticed = True
                    self._buffer.append(
                        _Pending(
                            datetime.now(UTC),
                            LogLevel.WARNING,
                            LogSource.SYSTEM,
                            "Log size limit reached for this deployment; further lines are dropped.",
                        )
                    )
                return
            self._used += size
            self._buffer.append(_Pending(timestamp or datetime.now(UTC), level, source, message))
            full = len(self._buffer) >= self._batch_size
        if full:
            self.flush()

    def info(self, message: str, source: LogSource = LogSource.DEPLOY) -> None:
        self.write(message, level=LogLevel.INFO, source=source)

    def warning(self, message: str, source: LogSource = LogSource.DEPLOY) -> None:
        self.write(message, level=LogLevel.WARNING, source=source)

    def error(self, message: str, source: LogSource = LogSource.DEPLOY) -> None:
        self.write(message, level=LogLevel.ERROR, source=source)

    # ------------------------------------------------------------------ flushing
    def _loop(self) -> None:
        while not self._closed.wait(self._flush_interval):
            try:
                self.flush()
            except Exception as exc:  # noqa: BLE001 — never kill the flusher thread
                log.warning("log_flush_failed", error=str(exc))

    def flush(self) -> None:
        with self._lock:
            batch, self._buffer = self._buffer, []
        if not batch:
            return
        rows = self._persist(batch)
        events.publish(
            self._redis,
            events.logs_channel(self.deployment_id),
            "logs",
            {"lines": rows},
        )

    def _persist(self, batch: list[_Pending]) -> list[dict[str, Any]]:
        values = [
            {
                "deployment_id": self.deployment_id,
                "timestamp": p.timestamp,
                "level": p.level,
                "source": p.source,
                "message": p.message,
            }
            for p in batch
        ]
        added = sum(len(p.message.encode("utf-8", errors="replace")) for p in batch)
        with self._session_factory() as session:
            ids = session.scalars(
                insert(DeploymentLog).returning(DeploymentLog.id, sort_by_parameter_order=True), values
            ).all()
            session.execute(
                update(Deployment)
                .where(Deployment.id == self.deployment_id)
                .values(
                    log_bytes=Deployment.log_bytes + added,
                    heartbeat_at=datetime.now(UTC),
                )
            )
        return [
            {
                "id": row_id,
                "deployment_id": str(self.deployment_id),
                "timestamp": v["timestamp"].isoformat(),
                "level": str(v["level"]),
                "source": str(v["source"]),
                "message": v["message"],
            }
            for row_id, v in zip(ids, values, strict=True)
            if isinstance(v["timestamp"], datetime)
        ]

    def close(self) -> None:
        self._closed.set()
        if self._flusher is not None:
            self._flusher.join(timeout=2)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                self.flush()
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("log_final_flush_failed", error=str(exc))
                time.sleep(0.5)

    def __enter__(self) -> LogSink:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
