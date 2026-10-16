"""Deterministic Docker resource names derived from platform identifiers."""

from __future__ import annotations

import uuid


def container_name(project_id: uuid.UUID, deployment_id: uuid.UUID) -> str:
    return f"paas-project-{project_id}-deployment-{deployment_id}"


def network_name(project_id: uuid.UUID) -> str:
    return f"paas-net-{project_id}"


def network_alias(deployment_id: uuid.UUID) -> str:
    """Short DNS name of a deployment inside its project network (labels must be ≤ 63 chars)."""
    return f"d-{deployment_id.hex[:16]}"
