"""Read side of logs and metrics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import Deployment, LogLevel, LogSource, Project, ProjectStatus
from app.repositories.deployments import DeploymentRepository
from app.repositories.observability import LogRepository, MetricRepository
from app.schemas.observability import LogLine, LogPage, MetricsResponse, MetricsSummary
from app.schemas.project import MetricSnapshot

MAX_SAMPLES = 720


class LogService:
    def __init__(self, session: AsyncSession) -> None:
        self.logs = LogRepository(session)
        self.deployments = DeploymentRepository(session)

    async def page(
        self,
        deployment: Deployment,
        *,
        after_id: int | None,
        limit: int,
        levels: list[LogLevel] | None,
        sources: list[LogSource] | None,
        tail: bool,
    ) -> LogPage:
        rows = await self.logs.list(
            deployment.id, after_id=after_id, limit=limit, levels=levels, sources=sources, tail=tail
        )
        items = [LogLine.model_validate(r) for r in rows]
        return LogPage(
            deployment_id=deployment.id,
            items=items,
            next_after_id=items[-1].id if items else after_id,
        )

    async def default_deployment(self, project: Project) -> Deployment | None:
        """The deployment whose logs a project's log view shows: in-progress, else latest."""
        active = await self.deployments.active_for_project(project.id)
        return active or await self.deployments.latest_for_project(project.id)


class MetricsService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.metrics = MetricRepository(session)
        self.settings = settings

    async def for_project(self, project: Project, window_minutes: int) -> MetricsResponse:
        since = datetime.now(UTC) - timedelta(minutes=window_minutes)
        rows = await self.metrics.since(project.id, since, MAX_SAMPLES)
        samples = [MetricSnapshot.model_validate(r) for r in rows]
        current = None
        if samples and project.status == ProjectStatus.RUNNING:
            latest = samples[-1]
            stale_after = timedelta(seconds=self.settings.metrics_interval_seconds * 3)
            if datetime.now(UTC) - latest.timestamp <= stale_after:
                current = MetricsSummary(**latest.model_dump(exclude={"timestamp"}))
        return MetricsResponse(
            project_id=project.id, window_minutes=window_minutes, current=current, samples=samples
        )
