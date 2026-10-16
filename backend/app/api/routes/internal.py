"""Internal endpoints consumed by platform components (never routed publicly by Traefik)."""

from __future__ import annotations

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.api.deps import DbSession, SettingsDep
from app.core.errors import NotFound
from app.routing.domains import DomainService
from app.routing.traefik import render_config, running_routes

router = APIRouter(prefix="/internal", include_in_schema=False)


@router.get("/traefik/config")
async def traefik_config(
    session: DbSession, settings: SettingsDep, token: Annotated[str, Query(max_length=256)] = ""
) -> dict[str, Any]:
    expected = settings.internal_api_token.get_secret_value()
    if not secrets.compare_digest(token.encode(), expected.encode()):
        raise NotFound()  # do not reveal that the endpoint exists
    return render_config(await running_routes(session), DomainService(settings))
