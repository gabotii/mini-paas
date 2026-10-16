"""Container lifecycle operations requested by users: stop, restart, delete."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

import redis

from app.builds.workspace import Storage
from app.core.config import Settings
from app.core.logging import get_logger
from app.deployments.naming import network_name
from app.docker.service import DockerError, DockerService
from app.logs.sink import LogSink
from app.models import AuditAction, LogSource
from app.workers.locks import project_lock
from app.workers.store import DeploymentStore, SessionFactory

log = get_logger(__name__)


class LifecycleService:
    def __init__(
        self,
        settings: Settings,
        docker: DockerService,
        store: DeploymentStore,
        storage: Storage,
        session_factory: SessionFactory,
        redis_client: redis.Redis,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.docker = docker
        self.store = store
        self.storage = storage
        self.sessions = session_factory
        self.redis = redis_client
        self.sleep = sleep

    def _note(self, deployment_id: uuid.UUID, message: str) -> None:
        with LogSink(
            self.sessions,
            self.redis,
            deployment_id,
            max_bytes=self.settings.max_log_bytes_per_deployment * 2,  # never drop lifecycle notes
            background=False,
        ) as sink:
            sink.info(message, source=LogSource.SYSTEM)

    def stop(self, project_id: uuid.UUID, user_id: uuid.UUID | None) -> None:
        self.store.request_cancel_active(project_id)
        with project_lock(self.redis, project_id):
            ref = self.store.mark_stopped(project_id, user_id=user_id)
            if ref is None:
                return
            if ref.container_id:
                try:
                    self.docker.stop_container(ref.container_id, timeout=10)
                    self.docker.remove_container(ref.container_id)
                except DockerError as exc:
                    log.warning("stop_container_failed", error=str(exc))
            self.docker.remove_network(network_name(project_id))
            self._note(ref.deployment_id, "Application stopped by user.")
        log.info("project_stopped", project_id=str(project_id))

    def restart(self, project_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        with project_lock(self.redis, project_id):
            deployment = self.store.current_running(project_id)
            if deployment is None or not deployment.container_id:
                return False
            self._note(deployment.id, "Restart requested by user...")
            self.docker.restart_container(deployment.container_id, timeout=10)
            self.store.audit(user_id, AuditAction.PROJECT_RESTARTED, "project", project_id)
            self._note(deployment.id, "Application restarted.")
        log.info("project_restarted", project_id=str(project_id))
        return True

    def delete(self, project_id: uuid.UUID) -> None:
        """Remove every Docker resource and file of a project, then the database rows."""
        self.store.request_cancel_active(project_id)
        # Give an in-flight pipeline time to observe the cancellation and clean up after itself.
        deadline = time.monotonic() + self.settings.health_check_timeout_seconds + 30
        while self.store.has_active(project_id) and time.monotonic() < deadline:
            self.sleep(1)
        with project_lock(self.redis, project_id):
            self.store.mark_stopped(project_id, user_id=None)
            for container in self.docker.list_managed_containers(include_stopped=True):
                if container.labels.get("paas.project_id") == str(project_id):
                    self.docker.remove_container(container.id)
            self.docker.remove_network(network_name(project_id))
            for image in self.docker.list_project_images(str(project_id)):
                for tag in image.get("RepoTags") or [image["Id"]]:
                    try:
                        self.docker.remove_image(tag)
                    except DockerError as exc:
                        log.warning("image_remove_failed", tag=tag, error=str(exc))
            self.storage.remove_project(project_id)
            self.store.delete_project(project_id)
        log.info("project_deleted", project_id=str(project_id))
