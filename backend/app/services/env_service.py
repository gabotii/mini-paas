"""Encrypted environment variables. Values are never logged; secrets are masked on read."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import SecretBox
from app.core.errors import Conflict, NotFound, QuotaExceeded, ValidationFailed
from app.models import AuditAction, EnvironmentVariable, Project, User
from app.repositories.env_vars import EnvVarRepository
from app.schemas.env import MASK, EnvVarCreate, EnvVarOut, EnvVarRevealed, EnvVarUpdate, validate_key
from app.services.audit import AuditService

RESERVED_PREFIX = "MINIPAAS_"


class EnvService:
    def __init__(
        self, session: AsyncSession, settings: Settings, audit: AuditService, box: SecretBox
    ) -> None:
        self.session = session
        self.settings = settings
        self.audit = audit
        self.box = box
        self.repo = EnvVarRepository(session)

    def _out(self, var: EnvironmentVariable) -> EnvVarOut:
        value = MASK if var.is_secret else self.box.decrypt(var.encrypted_value)
        return EnvVarOut(
            key=var.key,
            value=value,
            is_secret=var.is_secret,
            created_at=var.created_at,
            updated_at=var.updated_at,
        )

    def _check_value(self, value: str) -> None:
        if len(value.encode()) > self.settings.max_env_value_bytes:
            raise ValidationFailed(f"Values are limited to {self.settings.max_env_value_bytes} bytes")

    @staticmethod
    def _check_key(key: str) -> str:
        try:
            key = validate_key(key)
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from exc
        if key.upper().startswith(RESERVED_PREFIX):
            raise ValidationFailed(f"Keys starting with {RESERVED_PREFIX} are reserved by the platform")
        return key

    async def _get(self, project: Project, key: str) -> EnvironmentVariable:
        var = await self.repo.get(project.id, key)
        if var is None:
            raise NotFound(f"Variable {key} not found", code="ENV_NOT_FOUND")
        return var

    def _audit(self, user: User, project: Project, op: str, key: str) -> None:
        self.audit.record(
            AuditAction.ENV_UPDATED, user_id=user.id, resource_type="project", resource_id=project.id,
            details={"op": op, "key": key},
        )  # fmt: skip

    async def list(self, project: Project) -> list[EnvVarOut]:
        return [self._out(v) for v in await self.repo.list(project.id)]

    async def create(self, user: User, project: Project, data: EnvVarCreate) -> EnvVarOut:
        key = self._check_key(data.key)
        self._check_value(data.value)
        if await self.repo.count(project.id) >= self.settings.max_env_vars_per_project:
            raise QuotaExceeded(f"At most {self.settings.max_env_vars_per_project} variables per project")
        if await self.repo.get(project.id, key):
            raise Conflict(f"Variable {key} already exists", code="ENV_EXISTS")
        var = EnvironmentVariable(
            project_id=project.id,
            key=key,
            encrypted_value=self.box.encrypt(data.value),
            is_secret=data.is_secret,
        )
        try:
            async with self.session.begin_nested():
                await self.repo.add(var)
        except IntegrityError as exc:
            raise Conflict(f"Variable {key} already exists", code="ENV_EXISTS") from exc
        self._audit(user, project, "create", key)
        await self.session.commit()
        return self._out(var)

    async def update(self, user: User, project: Project, key: str, data: EnvVarUpdate) -> EnvVarOut:
        var = await self._get(project, key)
        if data.value is not None:
            self._check_value(data.value)
            var.encrypted_value = self.box.encrypt(data.value)
        if data.is_secret is not None:
            var.is_secret = data.is_secret
        self._audit(user, project, "update", key)
        await self.session.commit()
        await self.session.refresh(var)
        return self._out(var)

    async def delete(self, user: User, project: Project, key: str) -> None:
        var = await self._get(project, key)
        await self.repo.delete(var)
        self._audit(user, project, "delete", key)
        await self.session.commit()

    async def reveal(self, user: User, project: Project, key: str) -> EnvVarRevealed:
        var = await self._get(project, key)
        self.audit.record(
            AuditAction.ENV_REVEALED, user_id=user.id, resource_type="project", resource_id=project.id,
            details={"key": key},
        )  # fmt: skip
        await self.session.commit()
        return EnvVarRevealed(key=var.key, value=self.box.decrypt(var.encrypted_value))
