"""Per-project sub-resources: logs, metrics and environment variables."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import (
    CurrentUser,
    DeploymentServiceDep,
    EnvServiceDep,
    LogServiceDep,
    MetricsServiceDep,
    OwnedProject,
)
from app.core.errors import NotFound
from app.models import Deployment, LogLevel, LogSource
from app.schemas.env import EnvVarCreate, EnvVarOut, EnvVarRevealed, EnvVarUpdate
from app.schemas.observability import LogPage, MetricsResponse

router = APIRouter(prefix="/projects/{project_id}")


@router.get("/logs", response_model=LogPage, tags=["logs"], summary="Logs of the project's latest deployment")
async def project_logs(
    project: OwnedProject,
    user: CurrentUser,
    logs: LogServiceDep,
    deployments: DeploymentServiceDep,
    deployment_id: Annotated[uuid.UUID | None, Query()] = None,
    after_id: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
    level: Annotated[list[LogLevel] | None, Query()] = None,
    source: Annotated[list[LogSource] | None, Query()] = None,
    tail: Annotated[bool, Query()] = True,
) -> LogPage:
    deployment: Deployment | None
    if deployment_id is not None:
        deployment = await deployments.get_owned(deployment_id, user)
        if deployment.project_id != project.id:
            raise NotFound("Deployment not found", code="DEPLOYMENT_NOT_FOUND")
    else:
        deployment = await logs.default_deployment(project)
    if deployment is None:
        return LogPage(deployment_id=None, items=[], next_after_id=None)
    return await logs.page(
        deployment, after_id=after_id, limit=limit, levels=level, sources=source, tail=tail
    )


@router.get("/metrics", response_model=MetricsResponse, tags=["metrics"], summary="Resource usage samples")
async def project_metrics(
    project: OwnedProject,
    service: MetricsServiceDep,
    window: Annotated[int, Query(ge=5, le=1440, description="Window in minutes")] = 60,
) -> MetricsResponse:
    return await service.for_project(project, window)


@router.get(
    "/env", response_model=list[EnvVarOut], tags=["environment"], summary="List variables (secrets masked)"
)
async def list_env(project: OwnedProject, service: EnvServiceDep) -> list[EnvVarOut]:
    return await service.list(project)


@router.post(
    "/env",
    response_model=EnvVarOut,
    status_code=status.HTTP_201_CREATED,
    tags=["environment"],
    summary="Add a variable",
)
async def create_env(
    data: EnvVarCreate, project: OwnedProject, user: CurrentUser, service: EnvServiceDep
) -> EnvVarOut:
    return await service.create(user, project, data)


@router.patch("/env/{key}", response_model=EnvVarOut, tags=["environment"], summary="Update a variable")
async def update_env(
    key: str, data: EnvVarUpdate, project: OwnedProject, user: CurrentUser, service: EnvServiceDep
) -> EnvVarOut:
    return await service.update(user, project, key, data)


@router.delete(
    "/env/{key}", status_code=status.HTTP_204_NO_CONTENT, tags=["environment"], summary="Delete a variable"
)
async def delete_env(key: str, project: OwnedProject, user: CurrentUser, service: EnvServiceDep) -> Response:
    await service.delete(user, project, key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/env/{key}/reveal",
    response_model=EnvVarRevealed,
    tags=["environment"],
    summary="Reveal a secret value (audited)",
)
async def reveal_env(
    key: str, project: OwnedProject, user: CurrentUser, service: EnvServiceDep
) -> EnvVarRevealed:
    return await service.reveal(user, project, key)
