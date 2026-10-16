from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Deployment, DeploymentStatus, Project


class DeploymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, deployment: Deployment) -> Deployment:
        self.session.add(deployment)
        await self.session.flush()
        return deployment

    async def get_for_user(
        self, deployment_id: uuid.UUID, user_id: uuid.UUID, *, for_update: bool = False
    ) -> Deployment | None:
        stmt = (
            select(Deployment)
            .join(Project, Project.id == Deployment.project_id)
            .where(Deployment.id == deployment_id, Project.user_id == user_id)
        )
        if for_update:
            stmt = stmt.with_for_update(of=Deployment)
        return await self.session.scalar(stmt)

    async def list_for_project(
        self, project_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Deployment], int]:
        total = await self.session.scalar(
            select(func.count()).select_from(Deployment).where(Deployment.project_id == project_id)
        )
        rows = await self.session.scalars(
            select(Deployment)
            .where(Deployment.project_id == project_id)
            .order_by(Deployment.number.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows), int(total or 0)

    async def latest_for_project(self, project_id: uuid.UUID) -> Deployment | None:
        return await self.session.scalar(
            select(Deployment)
            .where(Deployment.project_id == project_id)
            .order_by(Deployment.number.desc())
            .limit(1)
        )

    async def active_for_project(self, project_id: uuid.UUID) -> Deployment | None:
        return await self.session.scalar(
            select(Deployment).where(
                Deployment.project_id == project_id,
                Deployment.status.in_(DeploymentStatus.active()),
            )
        )

    async def active_for_projects(self, project_ids: list[uuid.UUID]) -> dict[uuid.UUID, Deployment]:
        if not project_ids:
            return {}
        rows = await self.session.scalars(
            select(Deployment).where(
                Deployment.project_id.in_(project_ids),
                Deployment.status.in_(DeploymentStatus.active()),
            )
        )
        return {d.project_id: d for d in rows}

    async def count_active_for_user(self, user_id: uuid.UUID) -> int:
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(Deployment)
                .join(Project, Project.id == Deployment.project_id)
                .where(
                    Project.user_id == user_id,
                    Deployment.status.in_(DeploymentStatus.active()),
                )
            )
            or 0
        )
