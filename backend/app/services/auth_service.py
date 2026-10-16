"""Registration, login, refresh-token rotation and logout."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import Conflict, Unauthorized
from app.core.security import (
    AccessToken,
    create_access_token,
    generate_opaque_token,
    hash_password,
    hash_token,
    password_needs_rehash,
    verify_password,
)
from app.models import AuditAction, RefreshToken, User
from app.repositories.users import RefreshTokenRepository, UserRepository
from app.schemas.auth import LoginRequest, RegisterRequest
from app.services.audit import AuditService

ROTATION_GRACE = timedelta(seconds=30)


@dataclass(frozen=True)
class Session:
    user: User
    access: AccessToken
    refresh_token: str


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings, audit: AuditService) -> None:
        self.session = session
        self.settings = settings
        self.audit = audit
        self.users = UserRepository(session)
        self.tokens = RefreshTokenRepository(session)

    async def _issue(self, user: User, family_id: uuid.UUID | None = None) -> tuple[str, RefreshToken]:
        raw = generate_opaque_token()
        token = RefreshToken(
            id=uuid.uuid4(),
            user_id=user.id,
            token_hash=hash_token(raw),
            family_id=family_id or uuid.uuid4(),
            expires_at=datetime.now(UTC) + timedelta(days=self.settings.refresh_token_ttl_days),
            ip=self.audit.ctx.ip,
            user_agent=(self.audit.ctx.user_agent or "")[:255] or None,
        )
        await self.tokens.add(token)
        return raw, token

    def _access(self, user: User) -> AccessToken:
        return create_access_token(self.settings, user.id)

    async def register(self, data: RegisterRequest) -> Session:
        if await self.users.get_by_email(data.email):
            raise Conflict("An account with this email already exists", code="EMAIL_TAKEN")
        password_hash = await run_in_threadpool(hash_password, data.password)
        user = User(email=data.email, name=data.name, password_hash=password_hash)
        try:
            await self.users.add(user)
        except IntegrityError as exc:
            await self.session.rollback()
            raise Conflict("An account with this email already exists", code="EMAIL_TAKEN") from exc
        user.last_login_at = datetime.now(UTC)
        raw, _ = await self._issue(user)
        self.audit.record(
            AuditAction.USER_REGISTERED, user_id=user.id, resource_type="user", resource_id=user.id
        )
        await self.session.commit()
        return Session(user, self._access(user), raw)

    async def login(self, data: LoginRequest) -> Session:
        user = await self.users.get_by_email(data.email)
        valid = await run_in_threadpool(verify_password, data.password, user.password_hash if user else None)
        if not valid or user is None or not user.is_active:
            self.audit.record(
                AuditAction.USER_LOGIN_FAILED,
                user_id=user.id if user else None,
                details={"email": data.email},
            )
            await self.session.commit()
            raise Unauthorized("Invalid email or password", code="INVALID_CREDENTIALS")
        if password_needs_rehash(user.password_hash):
            user.password_hash = await run_in_threadpool(hash_password, data.password)
        user.last_login_at = datetime.now(UTC)
        raw, _ = await self._issue(user)
        self.audit.record(
            AuditAction.USER_LOGGED_IN, user_id=user.id, resource_type="user", resource_id=user.id
        )
        await self.session.commit()
        return Session(user, self._access(user), raw)

    async def refresh(self, raw_token: str | None) -> Session:
        if not raw_token:
            raise Unauthorized("Missing refresh token", code="INVALID_REFRESH_TOKEN")
        now = datetime.now(UTC)
        token = await self.tokens.get_by_hash_for_update(hash_token(raw_token))
        if token is None:
            raise Unauthorized("Invalid refresh token", code="INVALID_REFRESH_TOKEN")
        if token.revoked_at is not None:
            rotated_recently = (
                token.replaced_by_id is not None
                and now - token.revoked_at < ROTATION_GRACE
                and await self.tokens.family_active(token.family_id)
            )
            if not rotated_recently:
                # A rotated-away token was presented again: assume theft, kill the session family.
                await self.tokens.revoke_family(token.family_id, now)
                self.audit.record(AuditAction.REFRESH_TOKEN_REUSE, user_id=token.user_id)
                await self.session.commit()
                raise Unauthorized("Session expired, please log in again", code="INVALID_REFRESH_TOKEN")
        if token.expires_at <= now:
            raise Unauthorized("Session expired, please log in again", code="INVALID_REFRESH_TOKEN")
        user = await self.users.get(token.user_id)
        if user is None or not user.is_active:
            raise Unauthorized("Invalid refresh token", code="INVALID_REFRESH_TOKEN")
        raw, new_token = await self._issue(user, token.family_id)
        if token.revoked_at is None:
            token.revoked_at = now
            token.replaced_by_id = new_token.id
        await self.session.commit()
        return Session(user, self._access(user), raw)

    async def logout(self, raw_token: str | None, user_id: uuid.UUID | None) -> None:
        if raw_token:
            token = await self.tokens.get_by_hash_for_update(hash_token(raw_token))
            if token is not None:
                await self.tokens.revoke_family(token.family_id, datetime.now(UTC))
                user_id = user_id or token.user_id
        if user_id:
            self.audit.record(AuditAction.USER_LOGGED_OUT, user_id=user_id)
        await self.session.commit()
