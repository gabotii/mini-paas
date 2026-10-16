"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router, internal_router, ws_router
from app.api.routes.health import router as health_router
from app.api.websockets import connection_registry
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.core.redis import get_async_redis
from app.db.session import get_async_engine

log = get_logger(__name__)

OPENAPI_TAGS = [
    {"name": "auth", "description": "Registration, login, token refresh and logout."},
    {"name": "projects", "description": "Projects owned by the current user."},
    {"name": "deployments", "description": "Deploy, history, cancel and rollback."},
    {"name": "lifecycle", "description": "Stop and restart the running application."},
    {"name": "logs", "description": "Build, deploy and runtime logs (REST + WebSocket)."},
    {"name": "environment", "description": "Encrypted environment variables."},
    {"name": "metrics", "description": "CPU, memory and network samples from docker stats."},
    {"name": "health", "description": "Liveness and readiness probes."},
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    log.info("api_starting", environment=settings.environment)
    yield
    log.info("api_stopping", open_websockets=connection_registry.count)
    await connection_registry.close_all()
    await get_async_redis().aclose()
    await get_async_engine().dispose()
    log.info("api_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)

    app = FastAPI(
        title="Mini PaaS API",
        version="1.0.0",
        description=(
            "Deploy Python, Node.js, static and Dockerfile apps from a ZIP or a Git repository.\n\n"
            "Authenticate with `POST /api/auth/login` and send the access token as "
            "`Authorization: Bearer <token>`. Errors use a consistent envelope: "
            '`{"error": {"code", "message", "details"}}`.'
        ),
        openapi_tags=OPENAPI_TAGS,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    register_error_handlers(app)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        )
    app.add_middleware(RequestContextMiddleware)

    app.include_router(health_router)
    app.include_router(api_router, prefix="/api")
    app.include_router(ws_router)
    app.include_router(internal_router)
    return app


app = create_app()
