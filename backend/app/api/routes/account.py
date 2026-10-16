from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.repositories.observability import AuditRepository
from app.schemas.observability import AuditEntry

router = APIRouter(prefix="/account", tags=["auth"])


@router.get("/activity", response_model=list[AuditEntry], summary="My recent audit log entries")
async def activity(
    user: CurrentUser, session: DbSession, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[AuditEntry]:
    rows = await AuditRepository(session).list_for_user(user.id, limit)
    return [AuditEntry.model_validate(r) for r in rows]
