from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import AuditAction, AuditLog

log = get_logger("app.audit")


@dataclass(frozen=True)
class RequestContext:
    ip: str | None
    user_agent: str | None
    request_id: str | None


class AuditService:
    """Adds audit entries to the current unit of work (committed with the operation)."""

    def __init__(self, session: AsyncSession, ctx: RequestContext) -> None:
        self.session = session
        self.ctx = ctx

    def record(
        self,
        action: AuditAction,
        *,
        user_id: uuid.UUID | None,
        resource_type: str | None = None,
        resource_id: uuid.UUID | str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.session.add(
            AuditLog(
                user_id=user_id,
                action=str(action),
                resource_type=resource_type,
                resource_id=str(resource_id) if resource_id else None,
                ip=self.ctx.ip,
                request_id=self.ctx.request_id,
                details=details or {},
            )
        )
        log.info(
            "audit",
            action=str(action),
            user_id=str(user_id) if user_id else None,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else None,
        )
