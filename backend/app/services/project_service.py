"""Project management: create, read, update, delete — always scoped to the owner."""

from __future__ import annotations

import uuid

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.builds.git_source import GitSourceError, validate_repository_url
from app.core.config import Settings
from app.core.errors import Conflict, NotFound, QuotaExceeded, ServiceUnavailable, ValidationFailed
from app.models import AuditAction, Project, ProjectStatus, SourceType, User
from app.repositories.deployments import DeploymentRepository
from app.repositories.observability import MetricRepository
from app.repositories.projects import ProjectRepository
from app.routing.domains import DomainService, slugify, with_suffix
from app.schemas.deployment import DeploymentSummary
from app.schemas.project import MetricSnapshot, ProjectCreate, ProjectOut, ProjectUpdate
from app.services.audit import AuditService
from app.services.task_queue import TaskQueue


class ProjectService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        audit: AuditService,
        queue: TaskQueue,
    ) -> None:
        self.session = session
        self.settings = settings
        self.audit = audit
        self.queue = queue
        self.projects = ProjectRepository(session)
        self.deployments = DeploymentRepository(session)
        self.metrics = MetricRepository(session)
        self.domains = DomainService(settings)

    # ------------------------------------------------------------------ reads
    async def get_owned(self, project_id: uuid.UUID, user: User, *, for_update: bool = False) -> Project:
        project = await self.projects.get_for_user(project_id, user.id, for_update=for_update)
        if project is None:
            raise NotFound("Project not found", code="PROJECT_NOT_FOUND")
        return project

    async def list(self, user: User) -> list[ProjectOut]:
        projects = await self.projects.list_for_user(user.id)
        ids = [p.id for p in projects]
        active = await self.deployments.active_for_projects(ids)
        metrics = await self.metrics.latest_for_projects(ids)
        return [self.to_out(p, active.get(p.id), metrics.get(p.id)) for p in projects]

    async def detail(self, project: Project) -> ProjectOut:
        active = await self.deployments.active_for_project(project.id)
        metrics = await self.metrics.latest_for_projects([project.id])
        return self.to_out(project, active, metrics.get(project.id))

    def to_out(self, project: Project, active: object | None, metric: object | None) -> ProjectOut:
        current = project.current_deployment
        fresh_metric = metric if project.status == ProjectStatus.RUNNING else None
        return ProjectOut(
            id=project.id,
            name=project.name,
            slug=project.slug,
            description=project.description,
            runtime=project.runtime,
            source_type=project.source_type,
            repository_url=project.repository_url,
            repository_branch=project.repository_branch,
            has_source=project.source_type == SourceType.GIT or bool(project.source_archive),
            status=project.status,
            url=self.domains.app_url(project.slug),
            port=project.port,
            start_command=project.start_command,
            health_check_path=project.health_check_path,
            cpu_limit=project.cpu_limit,
            memory_limit_mb=project.memory_limit_mb,
            current_deployment=DeploymentSummary.model_validate(current) if current else None,
            active_deployment=DeploymentSummary.model_validate(active) if active else None,
            latest_metrics=MetricSnapshot.model_validate(fresh_metric) if fresh_metric else None,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )

    # ------------------------------------------------------------------ writes
    def _limits(self, cpu: float | None, memory: int | None) -> tuple[float, int]:
        cpu = cpu if cpu is not None else self.settings.app_cpu_default
        memory = memory if memory is not None else self.settings.app_memory_default_mb
        errors = []
        if cpu > self.settings.app_cpu_max:
            errors.append({"field": "cpu_limit", "message": f"Maximum is {self.settings.app_cpu_max} CPU"})
        if memory > self.settings.app_memory_max_mb:
            errors.append(
                {"field": "memory_limit_mb", "message": f"Maximum is {self.settings.app_memory_max_mb} MB"}
            )
        if errors:
            raise ValidationFailed("Resource limits exceed the platform maximum", details={"errors": errors})
        return cpu, memory

    async def _check_repository(self, url: str) -> str:
        try:
            repo = await run_in_threadpool(
                validate_repository_url, url, allow_private=self.settings.allow_private_git_hosts
            )
        except GitSourceError as exc:
            raise ValidationFailed(
                str(exc), details={"errors": [{"field": "repository_url", "message": str(exc)}]}
            ) from exc
        return repo.url

    async def create(self, user: User, data: ProjectCreate) -> Project:
        if await self.projects.count_for_user(user.id) >= self.settings.max_projects_per_user:
            raise QuotaExceeded(f"You can have at most {self.settings.max_projects_per_user} projects")
        cpu, memory = self._limits(data.cpu_limit, data.memory_limit_mb)
        repository_url = (
            await self._check_repository(data.repository_url)
            if data.source_type == SourceType.GIT and data.repository_url
            else None
        )

        if data.slug:
            if await self.projects.slug_exists(data.slug):
                raise Conflict(f"The subdomain '{data.slug}' is already taken", code="SLUG_TAKEN")
            slug = data.slug
        else:
            slug = slugify(data.name)
            attempts = 0
            while await self.projects.slug_exists(slug):
                attempts += 1
                if attempts > 5:
                    raise Conflict("Could not allocate a unique subdomain", code="SLUG_TAKEN")
                slug = with_suffix(slugify(data.name))

        project = Project(
            user_id=user.id,
            name=data.name,
            slug=slug,
            description=data.description,
            runtime=data.runtime,
            source_type=data.source_type,
            repository_url=repository_url,
            repository_branch=data.repository_branch,
            status=ProjectStatus.NEW,
            port=data.port,
            start_command=data.start_command,
            health_check_path=data.health_check_path,
            cpu_limit=cpu,
            memory_limit_mb=memory,
        )
        try:
            await self.projects.add(project)
        except IntegrityError as exc:
            await self.session.rollback()
            raise Conflict(f"The subdomain '{slug}' is already taken", code="SLUG_TAKEN") from exc
        self.audit.record(
            AuditAction.PROJECT_CREATED, user_id=user.id, resource_type="project", resource_id=project.id,
            details={"slug": slug, "runtime": str(data.runtime), "source_type": str(data.source_type)},
        )  # fmt: skip
        await self.session.commit()
        await self.session.refresh(project, attribute_names=["current_deployment"])
        return project

    async def update(self, user: User, project: Project, data: ProjectUpdate) -> Project:
        changes = data.model_dump(exclude_unset=True)
        if "name" in changes and (changes["name"] is None or not changes["name"].strip()):
            raise ValidationFailed("Name cannot be blank")
        if "health_check_path" in changes and changes["health_check_path"] is None:
            changes.pop("health_check_path")
        if "cpu_limit" in changes or "memory_limit_mb" in changes:
            cpu, memory = self._limits(
                changes.get("cpu_limit", project.cpu_limit),
                changes.get("memory_limit_mb", project.memory_limit_mb),
            )
            changes["cpu_limit"], changes["memory_limit_mb"] = cpu, memory
        if "repository_url" in changes:
            if project.source_type != SourceType.GIT:
                raise ValidationFailed("repository_url only applies to git projects")
            if not changes["repository_url"]:
                raise ValidationFailed("repository_url cannot be empty for git projects")
            changes["repository_url"] = await self._check_repository(changes["repository_url"])
        if "runtime" in changes and changes["runtime"] is None:
            changes.pop("runtime")
        for field, value in changes.items():
            setattr(project, field, value.strip() if field == "name" else value)
        self.audit.record(
            AuditAction.PROJECT_UPDATED, user_id=user.id, resource_type="project", resource_id=project.id,
            details={"fields": sorted(changes)},
        )  # fmt: skip
        await self.session.commit()
        await self.session.refresh(project, attribute_names=["current_deployment", "updated_at"])
        return project

    async def delete(self, user: User, project: Project) -> None:
        previous_status = project.status
        project.status = ProjectStatus.DELETING
        await self.session.commit()
        try:
            await self.queue.delete(project.id)
        except ServiceUnavailable:
            project.status = previous_status  # nothing will delete it: make it visible again
            await self.session.commit()
            raise
        self.audit.record(
            AuditAction.PROJECT_DELETED, user_id=user.id, resource_type="project", resource_id=project.id,
            details={"slug": project.slug},
        )  # fmt: skip
        await self.session.commit()
