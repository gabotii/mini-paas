from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, DeploymentServiceDep, LogServiceDep, OwnedDeployment, rate_limit
from app.models import LogLevel, LogSource
from app.schemas.deployment import DeploymentOut
from app.schemas.observability import LogPage

router = APIRouter(prefix="/deployments", tags=["deployments"])


@router.get("/{deployment_id}", response_model=DeploymentOut, summary="Deployment details")
async def get_deployment(deployment: OwnedDeployment, service: DeploymentServiceDep) -> DeploymentOut:
    return service.to_out(deployment)


@router.post(
    "/{deployment_id}/cancel", response_model=DeploymentOut, summary="Cancel a queued or running pipeline"
)
async def cancel_deployment(
    deployment: OwnedDeployment, user: CurrentUser, service: DeploymentServiceDep
) -> DeploymentOut:
    locked = await service.get_owned(deployment.id, user, for_update=True)
    return service.to_out(await service.cancel(user, locked))


@router.post(
    "/{deployment_id}/rollback",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DeploymentOut,
    dependencies=[Depends(rate_limit("deploy", "rate_limit_deploy", per="user"))],
    summary="Redeploy the image of a previous successful deployment",
)
async def rollback_deployment(
    deployment: OwnedDeployment, user: CurrentUser, service: DeploymentServiceDep
) -> DeploymentOut:
    return service.to_out(await service.rollback(user, deployment))


@router.get("/{deployment_id}/logs", response_model=LogPage, tags=["logs"], summary="Logs of a deployment")
async def deployment_logs(
    deployment: OwnedDeployment,
    service: LogServiceDep,
    after_id: Annotated[int | None, Query(ge=0, description="Return lines with id greater than this")] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
    level: Annotated[list[LogLevel] | None, Query()] = None,
    source: Annotated[list[LogSource] | None, Query()] = None,
    tail: Annotated[bool, Query(description="Return the last `limit` lines")] = False,
) -> LogPage:
    return await service.page(
        deployment, after_id=after_id, limit=limit, levels=level, sources=source, tail=tail
    )
