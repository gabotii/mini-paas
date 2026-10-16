"""Deployment orchestration on the API side: validation, quotas, persistence, enqueueing.

The heavy lifting happens in the worker; this service guarantees that what gets queued is
legal (ownership, quotas, one active deployment per project) and returns immediately.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.builds.archive import ArchiveError, ArchiveLimits, inspect_archive
from app.builds.workspace import Storage
from app.core.config import Settings
from app.core.errors import (
    Conflict,
    NotFound,
    PayloadTooLarge,
    QuotaExceeded,
    ServiceUnavailable,
    ValidationFailed,
)
from app.deployments.state_machine import transition
from app.models import (
    AuditAction,
    Deployment,
    DeploymentStatus,
    DeploymentTrigger,
    FailureReason,
    Project,
    ProjectStatus,
    SourceType,
    User,
)
from app.repositories.deployments import DeploymentRepository
from app.repositories.projects import ProjectRepository
from app.schemas.deployment import DeploymentOut
from app.services.audit import AuditService
from app.services.task_queue import TaskQueue


class DeploymentService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        audit: AuditService,
        queue: TaskQueue,
        storage: Storage,
    ) -> None:
        self.session = session
        self.settings = settings
        self.audit = audit
        self.queue = queue
        self.storage = storage
        self.deployments = DeploymentRepository(session)
        self.projects = ProjectRepository(session)

    # ------------------------------------------------------------------ reads
    async def get_owned(
        self, deployment_id: uuid.UUID, user: User, *, for_update: bool = False
    ) -> Deployment:
        deployment = await self.deployments.get_for_user(deployment_id, user.id, for_update=for_update)
        if deployment is None:
            raise NotFound("Deployment not found", code="DEPLOYMENT_NOT_FOUND")
        return deployment

    async def history(self, project: Project, *, limit: int, offset: int) -> tuple[list[Deployment], int]:
        return await self.deployments.list_for_project(project.id, limit=limit, offset=offset)

    def to_out(self, deployment: Deployment) -> DeploymentOut:
        out = DeploymentOut.model_validate(deployment)
        out.can_rollback = self.can_rollback(deployment)
        return out

    @staticmethod
    def can_rollback(deployment: Deployment) -> bool:
        return bool(
            deployment.succeeded
            and deployment.image_name
            and not (deployment.meta or {}).get("image_pruned")
            and deployment.status != DeploymentStatus.RUNNING
        )

    # ------------------------------------------------------------------ create
    async def _create(
        self,
        user: User,
        project: Project,
        *,
        trigger: DeploymentTrigger,
        branch: str | None = None,
        rollback_of: Deployment | None = None,
    ) -> Deployment:
        if project.status == ProjectStatus.DELETING:
            raise NotFound("Project not found", code="PROJECT_NOT_FOUND")
        if await self.deployments.active_for_project(project.id):
            raise Conflict(
                "A deployment is already in progress for this project", code="DEPLOYMENT_IN_PROGRESS"
            )
        if (
            await self.deployments.count_active_for_user(user.id)
            >= self.settings.max_active_deployments_per_user
        ):
            raise QuotaExceeded(
                f"At most {self.settings.max_active_deployments_per_user} deployments can run at the same time",
                code="TOO_MANY_DEPLOYMENTS",
            )
        if rollback_of is None and project.source_type == SourceType.ZIP and not project.source_archive:
            raise Conflict("Upload a ZIP archive before deploying", code="NO_SOURCE")

        number = await self.projects.next_deployment_number(project.id)
        deployment = Deployment(
            id=uuid.uuid4(),
            project_id=project.id,
            number=number,
            status=DeploymentStatus.QUEUED,
            trigger=trigger,
            source_type=project.source_type,
            source_archive=project.source_archive if project.source_type == SourceType.ZIP else None,
            repository_url=project.repository_url,
            branch=branch or project.repository_branch,
            created_by_id=user.id,
            heartbeat_at=datetime.now(UTC),
            meta={},
        )
        if rollback_of is not None:
            deployment.image_name = rollback_of.image_name
            deployment.runtime = rollback_of.runtime
            deployment.commit_hash = rollback_of.commit_hash
            deployment.source_archive = rollback_of.source_archive
            deployment.rollback_of_id = rollback_of.id
            deployment.meta = {"rollback_of_number": rollback_of.number}
        try:
            async with self.session.begin_nested():
                await self.deployments.add(deployment)
        except IntegrityError as exc:
            # Lost the race against a concurrent request: the partial unique index fired.
            raise Conflict(
                "A deployment is already in progress for this project", code="DEPLOYMENT_IN_PROGRESS"
            ) from exc

        action = AuditAction.ROLLBACK_STARTED if rollback_of else AuditAction.DEPLOYMENT_STARTED
        self.audit.record(
            action, user_id=user.id, resource_type="deployment", resource_id=deployment.id,
            details={"project_id": str(project.id), "number": number, "trigger": str(trigger)},
        )  # fmt: skip
        await self.session.commit()

        try:
            await self.queue.deploy(deployment.id)
        except ServiceUnavailable:
            transition(deployment, DeploymentStatus.FAILED, reason=FailureReason.INTERNAL_ERROR,
                       error="The job queue was unavailable")  # fmt: skip
            await self.session.commit()
            raise
        return deployment

    async def deploy(self, user: User, project: Project, branch: str | None = None) -> Deployment:
        return await self._create(user, project, trigger=DeploymentTrigger.DEPLOY, branch=branch)

    async def upload_and_deploy(
        self, user: User, project: Project, chunks: AsyncIterator[bytes]
    ) -> Deployment:
        if project.source_type != SourceType.ZIP:
            raise Conflict("This project deploys from a Git repository", code="WRONG_SOURCE_TYPE")
        if await self.deployments.active_for_project(project.id):
            raise Conflict(
                "A deployment is already in progress for this project", code="DEPLOYMENT_IN_PROGRESS"
            )

        path, relative = self.storage.new_archive_path(project.id)
        size = 0
        try:
            fh = await run_in_threadpool(path.open, "wb")
            try:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > self.settings.max_upload_bytes:
                        raise PayloadTooLarge(f"Archives are limited to {self.settings.max_upload_mb} MB")
                    await run_in_threadpool(fh.write, chunk)
            finally:
                await run_in_threadpool(fh.close)
            if size == 0:
                raise ValidationFailed("The uploaded file is empty")
            limits = ArchiveLimits(
                max_entries=self.settings.max_archive_entries,
                max_total_bytes=self.settings.max_extracted_bytes,
                max_ratio=self.settings.max_compression_ratio,
            )
            try:
                info = await run_in_threadpool(inspect_archive, path, limits)
            except ArchiveError as exc:
                raise ValidationFailed(str(exc), code="INVALID_ARCHIVE") from exc
        except BaseException:
            path.unlink(missing_ok=True)
            raise

        project.source_archive = relative
        self.audit.record(
            AuditAction.SOURCE_UPLOADED, user_id=user.id, resource_type="project", resource_id=project.id,
            details={"bytes": size, "files": info.file_count},
        )  # fmt: skip
        await self.session.flush()
        return await self._create(user, project, trigger=DeploymentTrigger.UPLOAD)

    async def rollback(self, user: User, target: Deployment) -> Deployment:
        if not self.can_rollback(target):
            raise Conflict(
                "Only previously successful deployments whose image still exists can be restored",
                code="ROLLBACK_NOT_POSSIBLE",
            )
        project = await self.projects.get_for_user(target.project_id, user.id)
        if project is None:
            raise NotFound("Project not found", code="PROJECT_NOT_FOUND")
        return await self._create(user, project, trigger=DeploymentTrigger.ROLLBACK, rollback_of=target)

    async def start(self, user: User, project: Project) -> Deployment:
        """Start a stopped/crashed project by redeploying the image of its last deployment."""
        if project.status == ProjectStatus.RUNNING:
            raise Conflict("The application is already running", code="ALREADY_RUNNING")
        current = project.current_deployment
        if current is None or not (current.image_name and current.succeeded):
            raise Conflict("Nothing to start yet — deploy the project first", code="NOTHING_TO_START")
        if (current.meta or {}).get("image_pruned"):
            raise Conflict("The image of the last deployment was removed; deploy again", code="IMAGE_GONE")
        return await self._create(user, project, trigger=DeploymentTrigger.ROLLBACK, rollback_of=current)

    async def cancel(self, user: User, deployment: Deployment) -> Deployment:
        if deployment.status == DeploymentStatus.QUEUED:
            transition(deployment, DeploymentStatus.CANCELLED)
        elif deployment.status in DeploymentStatus.active():
            deployment.cancel_requested = True
        else:
            raise Conflict("Only queued or in-progress deployments can be cancelled", code="NOT_CANCELLABLE")
        self.audit.record(
            AuditAction.DEPLOYMENT_CANCELLED,
            user_id=user.id,
            resource_type="deployment",
            resource_id=deployment.id,
        )
        await self.session.commit()
        return deployment

    # ------------------------------------------------------------------ lifecycle
    async def stop(self, user: User, project: Project) -> None:
        has_active = await self.deployments.active_for_project(project.id) is not None
        if project.status not in (ProjectStatus.RUNNING, ProjectStatus.CRASHED) and not has_active:
            raise Conflict("The application is not running", code="NOT_RUNNING")
        await self.queue.stop(project.id, user.id)

    async def restart(self, user: User, project: Project) -> None:
        if project.status != ProjectStatus.RUNNING:
            raise Conflict("Only running applications can be restarted", code="NOT_RUNNING")
        await self.queue.restart(project.id, user.id)
