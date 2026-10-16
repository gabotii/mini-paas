"""Celery task entry points. Thin wrappers: all logic lives in the pipeline/services."""

from __future__ import annotations

import uuid
from functools import lru_cache

from app.builds.build_service import BuildService
from app.builds.workspace import Storage
from app.core.config import get_settings
from app.core.crypto import get_secret_box
from app.core.logging import get_logger
from app.core.redis import get_sync_redis
from app.db.session import sync_session
from app.docker.service import DockerService
from app.services.heartbeats import beat
from app.workers.celery_app import celery_app
from app.workers.lifecycle import LifecycleService
from app.workers.maintenance import MaintenanceService
from app.workers.pipeline import DeploymentPipeline
from app.workers.store import DeploymentStore

log = get_logger(__name__)


@lru_cache
def _docker() -> DockerService:
    return DockerService()


def _store() -> DeploymentStore:
    return DeploymentStore(sync_session, get_sync_redis(), get_secret_box())


def _storage() -> Storage:
    return Storage(get_settings().data_dir)


def build_pipeline() -> DeploymentPipeline:
    settings = get_settings()
    return DeploymentPipeline(
        settings=settings,
        docker=_docker(),
        builds=BuildService(settings, _docker(), _storage()),
        store=_store(),
        session_factory=sync_session,
        redis_client=get_sync_redis(),
    )


def build_lifecycle() -> LifecycleService:
    return LifecycleService(get_settings(), _docker(), _store(), _storage(), sync_session, get_sync_redis())


_PIPELINE_LIMIT = get_settings().build_timeout_seconds + get_settings().health_check_timeout_seconds + 600


@celery_app.task(name="deployments.run", soft_time_limit=_PIPELINE_LIMIT, time_limit=_PIPELINE_LIMIT + 60)
def run_deployment(deployment_id: str) -> str | None:
    status = build_pipeline().run(uuid.UUID(deployment_id))
    return str(status) if status else None


@celery_app.task(name="projects.stop", soft_time_limit=300)
def stop_project(project_id: str, user_id: str | None) -> None:
    build_lifecycle().stop(uuid.UUID(project_id), uuid.UUID(user_id) if user_id else None)


@celery_app.task(name="projects.restart", soft_time_limit=300)
def restart_project(project_id: str, user_id: str) -> None:
    build_lifecycle().restart(uuid.UUID(project_id), uuid.UUID(user_id))


@celery_app.task(name="projects.delete", soft_time_limit=900)
def delete_project(project_id: str) -> None:
    build_lifecycle().delete(uuid.UUID(project_id))


def _maintenance() -> MaintenanceService:
    return MaintenanceService(get_settings(), sync_session, _store(), _docker(), _storage())


@celery_app.task(name="maintenance.reap_stale")
def reap_stale() -> int:
    return _maintenance().reap_stale_deployments()


@celery_app.task(name="maintenance.cleanup", soft_time_limit=1800)
def cleanup() -> dict[str, object]:
    return _maintenance().cleanup().__dict__


@celery_app.task(name="maintenance.heartbeat")
def heartbeat() -> None:
    beat(get_sync_redis(), "worker")
