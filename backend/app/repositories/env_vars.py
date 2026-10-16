from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EnvironmentVariable


class EnvVarRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(self, project_id: uuid.UUID) -> list[EnvironmentVariable]:
        rows = await self.session.scalars(
            select(EnvironmentVariable)
            .where(EnvironmentVariable.project_id == project_id)
            .order_by(EnvironmentVariable.key)
        )
        return list(rows)

    async def get(self, project_id: uuid.UUID, key: str) -> EnvironmentVariable | None:
        return await self.session.scalar(
            select(EnvironmentVariable).where(
                EnvironmentVariable.project_id == project_id, EnvironmentVariable.key == key
            )
        )

    async def count(self, project_id: uuid.UUID) -> int:
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(EnvironmentVariable)
                .where(EnvironmentVariable.project_id == project_id)
            )
            or 0
        )

    async def add(self, variable: EnvironmentVariable) -> EnvironmentVariable:
        self.session.add(variable)
        await self.session.flush()
        return variable

    async def delete(self, variable: EnvironmentVariable) -> None:
        await self.session.delete(variable)
        await self.session.flush()
