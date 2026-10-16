from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Request, status

from app.api.deps import (
    CurrentUser,
    DeploymentServiceDep,
    OwnedProject,
    ProjectServiceDep,
    SettingsDep,
    rate_limit,
)
from app.core.errors import PayloadTooLarge, ValidationFailed
from app.schemas.deployment import (
    ActionAccepted,
    DeploymentOut,
    DeploymentPage,
    DeploymentSummary,
    DeployRequest,
)
from app.schemas.project import ProjectCreate, ProjectList, ProjectOut, ProjectUpdate

router = APIRouter(prefix="/projects", tags=["projects"])

ZIP_CONTENT_TYPES = {"application/zip", "application/x-zip-compressed", "application/octet-stream"}


@router.get("", response_model=ProjectList, summary="List my projects")
async def list_projects(user: CurrentUser, service: ProjectServiceDep, settings: SettingsDep) -> ProjectList:
    return ProjectList(
        items=await service.list(user),
        limits={
            "max_projects": settings.max_projects_per_user,
            "max_upload_mb": settings.max_upload_mb,
            "cpu_max": settings.app_cpu_max,
            "memory_max_mb": settings.app_memory_max_mb,
            "cpu_default": settings.app_cpu_default,
            "memory_default_mb": settings.app_memory_default_mb,
        },
    )


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED, summary="Create a project")
async def create_project(data: ProjectCreate, user: CurrentUser, service: ProjectServiceDep) -> ProjectOut:
    project = await service.create(user, data)
    return service.to_out(project, None, None)


@router.get("/{project_id}", response_model=ProjectOut, summary="Project details")
async def get_project(project: OwnedProject, service: ProjectServiceDep) -> ProjectOut:
    return await service.detail(project)


@router.patch("/{project_id}", response_model=ProjectOut, summary="Update project settings")
async def update_project(
    data: ProjectUpdate, project: OwnedProject, user: CurrentUser, service: ProjectServiceDep
) -> ProjectOut:
    await service.update(user, project, data)
    return await service.detail(project)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ActionAccepted,
    summary="Delete a project (containers, images and data are removed in the background)",
)
async def delete_project(
    project: OwnedProject, user: CurrentUser, service: ProjectServiceDep
) -> ActionAccepted:
    await service.delete(user, project)
    return ActionAccepted(detail="Project scheduled for deletion")


@router.post(
    "/{project_id}/deploy",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DeploymentOut,
    tags=["deployments"],
    dependencies=[Depends(rate_limit("deploy", "rate_limit_deploy", per="user"))],
    summary="Start a deployment from the project's source",
)
async def deploy_project(
    project: OwnedProject,
    user: CurrentUser,
    service: DeploymentServiceDep,
    body: Annotated[DeployRequest | None, Body()] = None,
) -> DeploymentOut:
    deployment = await service.deploy(user, project, branch=body.branch if body else None)
    return service.to_out(deployment)


@router.post(
    "/{project_id}/upload",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DeploymentOut,
    tags=["deployments"],
    dependencies=[Depends(rate_limit("upload", "rate_limit_upload", per="user"))],
    summary="Upload a ZIP archive (raw request body) and deploy it",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def upload_source(
    request: Request,
    project: OwnedProject,
    user: CurrentUser,
    service: DeploymentServiceDep,
    settings: SettingsDep,
) -> DeploymentOut:
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in ZIP_CONTENT_TYPES:
        raise ValidationFailed("Send the ZIP archive as the request body with Content-Type: application/zip",
                               code="UNSUPPORTED_MEDIA_TYPE", status_code=415)  # fmt: skip
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > settings.max_upload_bytes:
        raise PayloadTooLarge(f"Archives are limited to {settings.max_upload_mb} MB")
    deployment = await service.upload_and_deploy(user, project, request.stream())
    return service.to_out(deployment)


@router.post(
    "/{project_id}/start",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DeploymentOut,
    tags=["lifecycle"],
    dependencies=[Depends(rate_limit("deploy", "rate_limit_deploy", per="user"))],
    summary="Start a stopped application from its last deployed image",
)
async def start_project(
    project: OwnedProject, user: CurrentUser, service: DeploymentServiceDep
) -> DeploymentOut:
    return service.to_out(await service.start(user, project))


@router.post(
    "/{project_id}/stop",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ActionAccepted,
    tags=["lifecycle"],
    summary="Stop the running application",
)
async def stop_project(
    project: OwnedProject, user: CurrentUser, service: DeploymentServiceDep
) -> ActionAccepted:
    await service.stop(user, project)
    return ActionAccepted(detail="Stopping application")


@router.post(
    "/{project_id}/restart",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ActionAccepted,
    tags=["lifecycle"],
    summary="Restart the running container",
)
async def restart_project(
    project: OwnedProject, user: CurrentUser, service: DeploymentServiceDep
) -> ActionAccepted:
    await service.restart(user, project)
    return ActionAccepted(detail="Restarting application")


@router.get(
    "/{project_id}/deployments",
    response_model=DeploymentPage,
    tags=["deployments"],
    summary="Deployment history (newest first)",
)
async def list_deployments(
    project: OwnedProject,
    service: DeploymentServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DeploymentPage:
    items, total = await service.history(project, limit=limit, offset=offset)
    return DeploymentPage(
        items=[DeploymentSummary.model_validate(d) for d in items], total=total, limit=limit, offset=offset
    )
