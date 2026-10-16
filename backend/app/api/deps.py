"""FastAPI dependencies: settings, DB session, current user, services and rate limits."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.builds.workspace import Storage
from app.core.config import Settings, get_settings
from app.core.crypto import SecretBox, SecretBoxError, get_secret_box
from app.core.errors import ServiceUnavailable, Unauthorized
from app.core.rate_limit import RateLimiter, Rule
from app.core.redis import get_async_redis
from app.core.security import decode_access_token
from app.db.session import get_db_session
from app.models import Deployment, Project, User
from app.services.audit import AuditService, RequestContext
from app.services.auth_service import AuthService
from app.services.deployment_service import DeploymentService
from app.services.env_service import EnvService
from app.services.observability_service import LogService, MetricsService
from app.services.project_service import ProjectService
from app.services.task_queue import CeleryTaskQueue, TaskQueue

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbSession = Annotated[AsyncSession, Depends(get_db_session)]

bearer_scheme = HTTPBearer(auto_error=False, description="Access token from /api/auth/login")


def client_ip(request: Request) -> str:
    # uvicorn runs with --proxy-headers behind Traefik, so request.client is the real client.
    return request.client.host if request.client else "unknown"


def request_context(request: Request) -> RequestContext:
    return RequestContext(
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        request_id=getattr(request.state, "request_id", None),
    )


ContextDep = Annotated[RequestContext, Depends(request_context)]


async def current_user(
    session: DbSession,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise Unauthorized(headers={"WWW-Authenticate": "Bearer"})
    user_id = decode_access_token(settings, credentials.credentials)
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise Unauthorized("Invalid access token", code="INVALID_TOKEN")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def get_task_queue() -> TaskQueue:
    return CeleryTaskQueue()


def get_storage(settings: SettingsDep) -> Storage:
    return Storage(settings.data_dir)


def get_rate_limiter(settings: SettingsDep) -> RateLimiter:
    return RateLimiter(get_async_redis(), enabled=settings.rate_limit_enabled)


def get_box() -> SecretBox:
    try:
        return get_secret_box()
    except SecretBoxError as exc:
        raise ServiceUnavailable(
            "Secret storage is not configured", code="ENCRYPTION_NOT_CONFIGURED"
        ) from exc


QueueDep = Annotated[TaskQueue, Depends(get_task_queue)]
StorageDep = Annotated[Storage, Depends(get_storage)]
LimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


def audit_service(session: DbSession, ctx: ContextDep) -> AuditService:
    return AuditService(session, ctx)


AuditDep = Annotated[AuditService, Depends(audit_service)]


def auth_service(session: DbSession, settings: SettingsDep, audit: AuditDep) -> AuthService:
    return AuthService(session, settings, audit)


def project_service(
    session: DbSession, settings: SettingsDep, audit: AuditDep, queue: QueueDep
) -> ProjectService:
    return ProjectService(session, settings, audit, queue)


def deployment_service(
    session: DbSession, settings: SettingsDep, audit: AuditDep, queue: QueueDep, storage: StorageDep
) -> DeploymentService:
    return DeploymentService(session, settings, audit, queue, storage)


def env_service(
    session: DbSession, settings: SettingsDep, audit: AuditDep, box: Annotated[SecretBox, Depends(get_box)]
) -> EnvService:
    return EnvService(session, settings, audit, box)


def log_service(session: DbSession) -> LogService:
    return LogService(session)


def metrics_service(session: DbSession, settings: SettingsDep) -> MetricsService:
    return MetricsService(session, settings)


AuthServiceDep = Annotated[AuthService, Depends(auth_service)]
ProjectServiceDep = Annotated[ProjectService, Depends(project_service)]
DeploymentServiceDep = Annotated[DeploymentService, Depends(deployment_service)]
EnvServiceDep = Annotated[EnvService, Depends(env_service)]
LogServiceDep = Annotated[LogService, Depends(log_service)]
MetricsServiceDep = Annotated[MetricsService, Depends(metrics_service)]


async def owned_project(project_id: uuid.UUID, user: CurrentUser, service: ProjectServiceDep) -> Project:
    return await service.get_owned(project_id, user)


async def owned_deployment(
    deployment_id: uuid.UUID, user: CurrentUser, service: DeploymentServiceDep
) -> Deployment:
    return await service.get_owned(deployment_id, user)


OwnedProject = Annotated[Project, Depends(owned_project)]
OwnedDeployment = Annotated[Deployment, Depends(owned_deployment)]


def rate_limit(scope: str, setting: str, *, per: str = "ip") -> Callable[..., Awaitable[None]]:
    """Dependency factory: throttle by client IP or by authenticated user."""

    if per == "user":

        async def by_user(user: CurrentUser, limiter: LimiterDep, settings: SettingsDep) -> None:
            await limiter.hit(scope, str(user.id), Rule.parse(getattr(settings, setting)))

        return by_user

    async def by_ip(request: Request, limiter: LimiterDep, settings: SettingsDep) -> None:
        await limiter.hit(scope, client_ip(request), Rule.parse(getattr(settings, setting)))

    return by_ip
