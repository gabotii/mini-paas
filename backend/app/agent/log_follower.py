"""Follows one container's stdout/stderr and feeds it to the deployment's LogSink."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta

import redis
from sqlalchemy import func, select

from app.core.logging import get_logger
from app.docker.service import DockerError, DockerService
from app.docker.stats import split_log_line
from app.logs.sink import LogSink
from app.models import Deployment, DeploymentLog, LogLevel, LogSource
from app.workers.store import SessionFactory

log = get_logger(__name__)


def guess_level(message: str) -> LogLevel:
    upper = message[:200].upper()
    if "ERROR" in upper or "TRACEBACK" in upper or "EXCEPTION" in upper or "FATAL" in upper:
        return LogLevel.ERROR
    if "WARN" in upper:
        return LogLevel.WARNING
    return LogLevel.INFO


class RateGate:
    """Per-second line budget; excess lines are counted and reported, not stored."""

    def __init__(self, per_second: int) -> None:
        self.per_second = per_second
        self.window = int(time.monotonic())
        self.count = 0
        self.dropped = 0

    def allow(self, now: float | None = None) -> tuple[bool, int]:
        """Returns (allowed, lines dropped in the previous window to report)."""
        second = int(now if now is not None else time.monotonic())
        report = 0
        if second != self.window:
            report, self.dropped = self.dropped, 0
            self.window, self.count = second, 0
        self.count += 1
        if self.count > self.per_second:
            self.dropped += 1
            return False, report
        return True, report


class LogFollower(threading.Thread):
    def __init__(
        self,
        docker: DockerService,
        sessions: SessionFactory,
        redis_client: redis.Redis,
        container_id: str,
        deployment_id: uuid.UUID,
        *,
        max_bytes: int,
        lines_per_second: int,
    ) -> None:
        super().__init__(name=f"logs-{container_id[:12]}", daemon=True)
        self.docker = docker
        self.sessions = sessions
        self.redis = redis_client
        self.container_id = container_id
        self.deployment_id = deployment_id
        self.max_bytes = max_bytes
        self.gate = RateGate(lines_per_second)
        self.stopping = threading.Event()

    def _resume_point(self) -> tuple[int, datetime | None]:
        with self.sessions() as session:
            used = (
                session.scalar(select(Deployment.log_bytes).where(Deployment.id == self.deployment_id)) or 0
            )
            last = session.scalar(
                select(func.max(DeploymentLog.timestamp)).where(
                    DeploymentLog.deployment_id == self.deployment_id,
                    DeploymentLog.source == LogSource.RUNTIME,
                )
            )
        return int(used), last

    def run(self) -> None:
        try:
            used, last = self._resume_point()
        except Exception as exc:  # noqa: BLE001
            log.warning("log_follower_init_failed", container=self.container_id[:12], error=str(exc))
            return
        since = int((last + timedelta(seconds=1)).timestamp()) if last else None
        sink = LogSink(
            self.sessions, self.redis, self.deployment_id, max_bytes=self.max_bytes, used_bytes=used
        )
        buffer = b""
        try:
            for chunk in self.docker.stream_container_logs(self.container_id, since=since):
                if self.stopping.is_set():
                    break
                buffer += chunk
                *lines, buffer = buffer.split(b"\n")
                for raw in lines:
                    self._emit(sink, raw.decode("utf-8", errors="replace").rstrip("\r"))
            if buffer:
                self._emit(sink, buffer.decode("utf-8", errors="replace"))
        except DockerError as exc:
            log.info("log_stream_ended", container=self.container_id[:12], error=str(exc))
        except Exception as exc:  # noqa: BLE001 — a broken stream must not kill the agent
            log.warning("log_stream_failed", container=self.container_id[:12], error=type(exc).__name__)
        finally:
            sink.close()

    def _emit(self, sink: LogSink, line: str) -> None:
        if not line.strip():
            return
        allowed, dropped = self.gate.allow()
        if dropped:
            sink.write(
                f"[{dropped} log lines dropped: rate limit exceeded]",
                level=LogLevel.WARNING,
                source=LogSource.SYSTEM,
            )
        if not allowed:
            return
        timestamp, message = split_log_line(line)
        sink.write(message, level=guess_level(message), source=LogSource.RUNTIME, timestamp=timestamp)

    def stop(self) -> None:
        self.stopping.set()
