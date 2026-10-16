"""Abstraction over the background job queue so the API never imports worker code paths
and tests can substitute an in-memory recorder."""

from __future__ import annotations

import uuid
from typing import Protocol

from fastapi.concurrency import run_in_threadpool

from app.core.errors import ServiceUnavailable
from app.core.logging import get_logger

log = get_logger(__name__)

_RETRY = {"max_retries": 2, "interval_start": 0, "interval_step": 0.5, "interval_max": 1}


class TaskQueue(Protocol):
    async def deploy(self, deployment_id: uuid.UUID) -> None: ...
    async def stop(self, project_id: uuid.UUID, user_id: uuid.UUID) -> None: ...
    async def restart(self, project_id: uuid.UUID, user_id: uuid.UUID) -> None: ...
    async def delete(self, project_id: uuid.UUID) -> None: ...


class CeleryTaskQueue:
    async def _send(self, name: str, *args: str | None) -> None:
        from app.workers.celery_app import celery_app

        def send() -> None:
            celery_app.send_task(name, args=list(args), retry=True, retry_policy=_RETRY)

        try:
            await run_in_threadpool(send)
        except Exception as exc:  # kombu raises OperationalError and friends
            log.error("enqueue_failed", task=name, error=str(exc))
            raise ServiceUnavailable(
                "The job queue is unavailable, try again shortly", code="QUEUE_UNAVAILABLE"
            ) from exc

    async def deploy(self, deployment_id: uuid.UUID) -> None:
        await self._send("deployments.run", str(deployment_id))

    async def stop(self, project_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await self._send("projects.stop", str(project_id), str(user_id))

    async def restart(self, project_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await self._send("projects.restart", str(project_id), str(user_id))

    async def delete(self, project_id: uuid.UUID) -> None:
        await self._send("projects.delete", str(project_id))
