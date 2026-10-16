from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Project, ProjectStatus


class ProjectRepository:
    """All reads are scoped to an owner: a project of another user is simply 'not found'."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_user(self, user_id: uuid.UUID) -> list[Project]:
        result = await self.session.scalars(
            select(Project)
            .where(Project.user_id == user_id, Project.status != ProjectStatus.DELETING)
            .options(selectinload(Project.current_deployment))
            .order_by(Project.created_at.desc())
        )
        return list(result)

    async def get_for_user(
        self, project_id: uuid.UUID, user_id: uuid.UUID, *, for_update: bool = False
    ) -> Project | None:
        stmt = (
            select(Project)
            .where(
                Project.id == project_id,
                Project.user_id == user_id,
                Project.status != ProjectStatus.DELETING,
            )
            .options(selectinload(Project.current_deployment))
        )
        if for_update:
            stmt = stmt.with_for_update(of=Project)
        return await self.session.scalar(stmt)

    async def count_for_user(self, user_id: uuid.UUID) -> int:
        return int(
            await self.session.scalar(
                select(func.count()).select_from(Project).where(Project.user_id == user_id)
            )
            or 0
        )

    async def slug_exists(self, slug: str) -> bool:
        return bool(await self.session.scalar(select(Project.id).where(Project.slug == slug)))

    async def add(self, project: Project) -> Project:
        self.session.add(project)
        await self.session.flush()
        return project

    async def next_deployment_number(self, project_id: uuid.UUID) -> int:
        """Atomically increments the per-project counter (row-level lock, no gaps race)."""
        number = await self.session.scalar(
            update(Project)
            .where(Project.id == project_id)
            .values(deployment_counter=Project.deployment_counter + 1)
            .returning(Project.deployment_counter)
        )
        assert number is not None
        return int(number)
