"""Synchronous data access for the deployment worker and the agent.



Each method is one short transaction — no transaction is ever held open across a Docker

build or a health check. Every status change goes through the state machine and is

published to the owner's event channel after commit.

"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import SecretBox
from app.core.logging import get_logger
from app.deployments import events
from app.deployments.state_machine import transition
from app.models import (
    AuditAction,
    AuditLog,
    Deployment,
    DeploymentStatus,
    DeploymentTrigger,
    EnvironmentVariable,
    FailureReason,
    Project,
    ProjectStatus,
    Runtime,
    SourceType,
)
from app.schemas.deployment import DeploymentSummary

log = get_logger(__name__)


SessionFactory = Callable[[], AbstractContextManager[Session]]


@dataclass(frozen=True)
class DeploymentContext:

    deployment_id: uuid.UUID

    number: int

    trigger: DeploymentTrigger

    project_id: uuid.UUID

    user_id: uuid.UUID

    slug: str

    source_type: SourceType

    source_archive: str | None

    repository_url: str | None

    branch: str | None

    requested_runtime: Runtime

    start_command: str | None

    port: int | None

    health_check_path: str

    cpu_limit: float

    memory_limit_mb: int

    log_bytes: int

    environment: dict[str, str]

    # rollback only

    image_name: str | None = None

    rollback_runtime: Runtime | None = None

    rollback_port: int | None = None


@dataclass(frozen=True)
class RunningRef:

    deployment_id: uuid.UUID

    container_id: str | None


class DeploymentStore:

    def __init__(
        self, session_factory: SessionFactory, redis_client: redis.Redis, secret_box: SecretBox
    ) -> None:

        self._sessions = session_factory

        self._redis = redis_client

        self._box = secret_box

    # ------------------------------------------------------------------ events

    def _publish_deployment(
        self, deployment: Deployment, user_id: uuid.UUID, project_status: ProjectStatus
    ) -> None:

        payload = DeploymentSummary.model_validate(deployment).model_dump(mode="json")

        payload["project_status"] = str(project_status)

        payload["error_message"] = deployment.error_message

        events.publish(self._redis, events.user_channel(user_id), "deployment.updated", payload)

    def publish_project(self, project: Project) -> None:

        events.publish(
            self._redis,
            events.user_channel(project.user_id),
            "project.updated",
            {
                "id": str(project.id),
                "status": str(project.status),
                "current_deployment_id": str(project.current_deployment_id or "") or None,
            },
        )

    # ------------------------------------------------------------------ pipeline

    def claim(self, deployment_id: uuid.UUID) -> DeploymentContext | None:
        """Load a QUEUED deployment with everything the pipeline needs, or None to skip."""

        with self._sessions() as session:

            deployment = session.get(Deployment, deployment_id)

            if deployment is None or deployment.status != DeploymentStatus.QUEUED:

                return None

            project = session.get(Project, deployment.project_id)

            if project is None or project.status == ProjectStatus.DELETING:

                return None

            env_rows = session.scalars(
                select(EnvironmentVariable).where(EnvironmentVariable.project_id == project.id)
            ).all()

            environment = {row.key: self._box.decrypt(row.encrypted_value) for row in env_rows}

            rollback_runtime = rollback_port = None

            if deployment.rollback_of_id:

                target = session.get(Deployment, deployment.rollback_of_id)

                if target is not None:

                    rollback_runtime, rollback_port = target.runtime, target.internal_port

            return DeploymentContext(
                deployment_id=deployment.id,
                number=deployment.number,
                trigger=deployment.trigger,
                project_id=project.id,
                user_id=project.user_id,
                slug=project.slug,
                source_type=deployment.source_type,
                source_archive=deployment.source_archive,
                repository_url=deployment.repository_url,
                branch=deployment.branch,
                requested_runtime=project.runtime,
                start_command=project.start_command,
                port=project.port,
                health_check_path=project.health_check_path,
                cpu_limit=project.cpu_limit,
                memory_limit_mb=project.memory_limit_mb,
                log_bytes=deployment.log_bytes,
                environment=environment,
                image_name=deployment.image_name,
                rollback_runtime=rollback_runtime,
                rollback_port=rollback_port,
            )

    def transition(
        self,
        deployment_id: uuid.UUID,
        target: DeploymentStatus,
        *,
        reason: FailureReason | None = None,
        error: str | None = None,
        **fields: Any,
    ) -> None:

        with self._sessions() as session:

            project, deployment = self._lock_pair(session, deployment_id)

            if project is None or deployment is None:

                log.warning("transition_target_missing", deployment_id=str(deployment_id))

                return

            transition(deployment, target, reason=reason, error=error)

            for name, value in fields.items():

                setattr(deployment, name, value)

            deployment.heartbeat_at = datetime.now(UTC)

            if target == DeploymentStatus.FAILED and not self._serving(session, project):

                project.status = ProjectStatus.FAILED

            session.flush()

            user_id, project_status = project.user_id, project.status

        self._publish_deployment(deployment, user_id, project_status)

    @staticmethod
    def _lock_pair(session: Session, deployment_id: uuid.UUID) -> tuple[Project | None, Deployment | None]:
        """Lock project then deployment — one global lock order prevents deadlocks."""

        project_id = session.scalar(select(Deployment.project_id).where(Deployment.id == deployment_id))

        if project_id is None:

            return None, None

        project = session.get(Project, project_id, with_for_update=True)

        deployment = session.get(Deployment, deployment_id, with_for_update=True, populate_existing=True)

        return project, deployment

    def _serving(self, session: Session, project: Project) -> bool:

        if project.current_deployment_id is None:

            return False

        current = session.get(Deployment, project.current_deployment_id)

        return current is not None and current.status == DeploymentStatus.RUNNING

    def update(self, deployment_id: uuid.UUID, **fields: Any) -> None:

        with self._sessions() as session:

            deployment = session.get(Deployment, deployment_id)

            assert deployment is not None

            for name, value in fields.items():

                setattr(deployment, name, value)

            deployment.heartbeat_at = datetime.now(UTC)

    def merge_meta(self, deployment_id: uuid.UUID, values: dict[str, Any]) -> None:

        with self._sessions() as session:

            deployment = session.get(Deployment, deployment_id)

            assert deployment is not None

            deployment.meta = {**(deployment.meta or {}), **values}

    def cancel_requested(self, deployment_id: uuid.UUID) -> bool:

        with self._sessions() as session:

            return bool(
                session.scalar(select(Deployment.cancel_requested).where(Deployment.id == deployment_id))
            )

    def go_live(self, deployment_id: uuid.UUID) -> RunningRef | None:
        """HEALTH_CHECKING → RUNNING and point the project at it (routes follow).



        The previously serving deployment is marked STOPPED *before* its container is

        stopped, so the crash watcher never mistakes the shutdown for a crash.

        Returns the previous deployment whose container must now be removed."""

        with self._sessions() as session:

            project, deployment = self._lock_pair(session, deployment_id)

            assert project is not None
            assert deployment is not None

            transition(deployment, DeploymentStatus.RUNNING)

            previous: RunningRef | None = None

            old: Deployment | None = None

            if project.current_deployment_id and project.current_deployment_id != deployment.id:

                old = session.get(Deployment, project.current_deployment_id, with_for_update=True)

                if old is not None and old.status == DeploymentStatus.RUNNING:

                    transition(old, DeploymentStatus.STOPPED)

                    old.meta = {**(old.meta or {}), "superseded_by": deployment.number}

                    previous = RunningRef(old.id, old.container_id)

                elif old is not None:

                    previous = RunningRef(old.id, old.container_id)

            project.current_deployment_id = deployment.id

            project.status = ProjectStatus.RUNNING

            session.flush()

            user_id = project.user_id

            self._audit(session, user_id, AuditAction.DEPLOYMENT_SUCCEEDED, "deployment", deployment.id,

                        {"number": deployment.number, "project_id": str(project.id)})  # fmt: skip

        self._publish_deployment(deployment, user_id, ProjectStatus.RUNNING)

        if old is not None and old.status == DeploymentStatus.STOPPED:

            self._publish_deployment(old, user_id, ProjectStatus.RUNNING)

        self.publish_project(project)

        return previous

    # ------------------------------------------------------------------ lifecycle

    def project(self, project_id: uuid.UUID) -> Project | None:

        with self._sessions() as session:

            return session.get(Project, project_id)

    def current_running(self, project_id: uuid.UUID) -> Deployment | None:

        with self._sessions() as session:

            project = session.get(Project, project_id)

            if project is None or project.current_deployment_id is None:

                return None

            deployment = session.get(Deployment, project.current_deployment_id)

            if deployment is None or deployment.status != DeploymentStatus.RUNNING:

                return None

            return deployment

    def request_cancel_active(self, project_id: uuid.UUID) -> None:

        with self._sessions() as session:

            for deployment in session.scalars(
                select(Deployment).where(
                    Deployment.project_id == project_id,
                    Deployment.status.in_(DeploymentStatus.active()),
                )
            ):

                deployment.cancel_requested = True

    def mark_stopped(self, project_id: uuid.UUID, *, user_id: uuid.UUID | None) -> RunningRef | None:
        """Mark the serving deployment STOPPED and the project STOPPED (before stopping Docker)."""

        with self._sessions() as session:

            project = session.get(Project, project_id, with_for_update=True)

            if project is None:

                return None

            ref: RunningRef | None = None

            deployment: Deployment | None = None

            if project.current_deployment_id:

                deployment = session.get(Deployment, project.current_deployment_id, with_for_update=True)

                if deployment is not None:

                    if deployment.status == DeploymentStatus.RUNNING:

                        transition(deployment, DeploymentStatus.STOPPED)

                    ref = RunningRef(deployment.id, deployment.container_id)

            if project.status != ProjectStatus.DELETING:

                project.status = ProjectStatus.STOPPED

            session.flush()

            if user_id:

                self._audit(session, user_id, AuditAction.PROJECT_STOPPED, "project", project.id, {})

            owner = project.user_id

            status = project.status

        if deployment is not None:

            self._publish_deployment(deployment, owner, status)

        self.publish_project(project)

        return ref

    def mark_crashed(self, deployment_id: uuid.UUID, message: str) -> bool:
        """RUNNING → FAILED(RUNTIME_CRASHED). Returns False if the deployment is no longer running."""

        with self._sessions() as session:

            project, deployment = self._lock_pair(session, deployment_id)

            if project is None or deployment is None or deployment.status != DeploymentStatus.RUNNING:

                return False

            transition(
                deployment, DeploymentStatus.FAILED, reason=FailureReason.RUNTIME_CRASHED, error=message
            )

            if project.current_deployment_id == deployment.id:

                project.status = ProjectStatus.CRASHED

            session.flush()

            self._audit(session, project.user_id, AuditAction.DEPLOYMENT_FAILED, "deployment", deployment.id,

                        {"reason": "RUNTIME_CRASHED", "message": message})  # fmt: skip

            owner, status = project.user_id, project.status

        self._publish_deployment(deployment, owner, status)

        self.publish_project(project)

        return True

    def has_active(self, project_id: uuid.UUID) -> bool:

        with self._sessions() as session:

            return bool(
                session.scalar(
                    select(Deployment.id).where(
                        Deployment.project_id == project_id,
                        Deployment.status.in_(DeploymentStatus.active()),
                    )
                )
            )

    def delete_project(self, project_id: uuid.UUID) -> None:

        with self._sessions() as session:

            project = session.get(Project, project_id)

            if project is not None:

                project.current_deployment_id = None

                session.flush()

                session.delete(project)

    # ------------------------------------------------------------------ audit

    def _audit(
        self,
        session: Session,
        user_id: uuid.UUID | None,
        action: AuditAction,
        resource_type: str,
        resource_id: uuid.UUID,
        details: dict[str, Any],
    ) -> None:

        session.add(
            AuditLog(
                user_id=user_id,
                action=str(action),
                resource_type=resource_type,
                resource_id=str(resource_id),
                details=details,
            )
        )

    def audit(
        self,
        user_id: uuid.UUID | None,
        action: AuditAction,
        resource_type: str,
        resource_id: uuid.UUID,
        details: dict[str, Any] | None = None,
    ) -> None:

        with self._sessions() as session:

            self._audit(session, user_id, action, resource_type, resource_id, details or {})
