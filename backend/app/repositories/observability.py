from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, DeploymentLog, LogLevel, LogSource, ProjectMetric


class LogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(
        self,
        deployment_id: uuid.UUID,
        *,
        after_id: int | None = None,
        limit: int = 500,
        levels: list[LogLevel] | None = None,
        sources: list[LogSource] | None = None,
        tail: bool = False,
    ) -> list[DeploymentLog]:
        stmt = select(DeploymentLog).where(DeploymentLog.deployment_id == deployment_id)
        if after_id is not None:
            stmt = stmt.where(DeploymentLog.id > after_id)
        if levels:
            stmt = stmt.where(DeploymentLog.level.in_(levels))
        if sources:
            stmt = stmt.where(DeploymentLog.source.in_(sources))
        if tail:
            # Last N lines, returned in chronological order.
            rows = list(await self.session.scalars(stmt.order_by(DeploymentLog.id.desc()).limit(limit)))
            rows.reverse()
            return rows
        return list(await self.session.scalars(stmt.order_by(DeploymentLog.id).limit(limit)))


class MetricRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def since(self, project_id: uuid.UUID, since: datetime, limit: int) -> list[ProjectMetric]:
        rows = list(
            await self.session.scalars(
                select(ProjectMetric)
                .where(ProjectMetric.project_id == project_id, ProjectMetric.timestamp >= since)
                .order_by(ProjectMetric.timestamp.desc())
                .limit(limit)
            )
        )
        rows.reverse()
        return rows

    async def latest_for_projects(self, project_ids: list[uuid.UUID]) -> dict[uuid.UUID, ProjectMetric]:
        """Latest sample per project in one query (DISTINCT ON), avoiding N+1 on the dashboard."""
        if not project_ids:
            return {}
        rows = await self.session.scalars(
            select(ProjectMetric)
            .where(ProjectMetric.project_id.in_(project_ids))
            .ext(distinct_on(ProjectMetric.project_id))
            .order_by(ProjectMetric.project_id, ProjectMetric.timestamp.desc())
        )
        return {m.project_id: m for m in rows}


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, entry: AuditLog) -> None:
        self.session.add(entry)

    async def list_for_user(self, user_id: uuid.UUID, limit: int) -> list[AuditLog]:
        rows = await self.session.scalars(
            select(AuditLog).where(AuditLog.user_id == user_id).order_by(AuditLog.id.desc()).limit(limit)
        )
        return list(rows)
