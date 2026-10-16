from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import account, auth, deployments, internal, project_resources, projects
from app.api.websockets import router as ws_router

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(account.router)
api_router.include_router(projects.router)
api_router.include_router(project_resources.router)
api_router.include_router(deployments.router)

internal_router = internal.router

__all__ = ["api_router", "internal_router", "ws_router"]
