from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshToken, User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        return await self.session.scalar(select(User).where(User.email == email))

    async def add(self, user: User) -> User:
        self.session.add(user)
        await self.session.flush()
        return user


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, token: RefreshToken) -> None:
        self.session.add(token)
        await self.session.flush()

    async def get_by_hash_for_update(self, token_hash: str) -> RefreshToken | None:
        # Row lock: two concurrent refreshes with the same token cannot both succeed.
        return await self.session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
        )

    async def family_active(self, family_id: uuid.UUID) -> bool:
        """False once the family was revoked (logout or reuse detection)."""
        return bool(
            await self.session.scalar(
                select(RefreshToken.id).where(
                    RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None)
                )
            )
        )

    async def revoke_family(self, family_id: uuid.UUID, now: datetime) -> None:
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
