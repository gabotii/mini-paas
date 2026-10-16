"""Traefik dynamic configuration rendered from the database.

Traefik's HTTP provider polls `GET /internal/traefik/config` every couple of seconds. The
response routes each running project's hostnames to its current deployment's network alias.
The database is the only source of truth: there is no proxy state to drift or reconcile.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deployments.naming import network_alias
from app.models import Deployment, DeploymentStatus, Project, ProjectStatus
from app.routing.domains import DomainService


@dataclass(frozen=True)
class Route:
    slug: str
    deployment_id: uuid.UUID
    port: int


async def running_routes(session: AsyncSession) -> list[Route]:
    rows = await session.execute(
        select(Project.slug, Deployment.id, Deployment.internal_port)
        .join(Deployment, Deployment.id == Project.current_deployment_id)
        .where(
            Project.status == ProjectStatus.RUNNING,
            Deployment.status == DeploymentStatus.RUNNING,
            Deployment.internal_port.is_not(None),
        )
        .order_by(Project.slug)
    )
    return [Route(slug=slug, deployment_id=dep_id, port=port) for slug, dep_id, port in rows if port]


def render_config(routes: list[Route], domains: DomainService) -> dict[str, Any]:
    routers: dict[str, Any] = {}
    services: dict[str, Any] = {}
    for route in routes:
        name = f"app-{route.slug}"
        rule = " || ".join(f"Host(`{host}`)" for host in domains.hosts_for(route.slug))
        routers[name] = {
            "rule": rule,
            "entryPoints": ["web"],
            "service": name,
            "priority": 100,
            "middlewares": ["app-headers@file"],
        }
        services[name] = {
            "loadBalancer": {
                "servers": [{"url": f"http://{network_alias(route.deployment_id)}:{route.port}"}],
                "passHostHeader": True,
            }
        }
    if not routers:
        return {}
    return {"http": {"routers": routers, "services": services}}
