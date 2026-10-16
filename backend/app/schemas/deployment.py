from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models import DeploymentStatus, DeploymentTrigger, FailureReason, Runtime, SourceType


class DeploymentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    number: int
    status: DeploymentStatus
    trigger: DeploymentTrigger
    runtime: Runtime | None
    commit_hash: str | None
    failure_reason: FailureReason | None
    succeeded: bool
    duration_seconds: float | None
    created_at: datetime
    started_at: datetime | None
    deployed_at: datetime | None
    finished_at: datetime | None
    stopped_at: datetime | None


class DeploymentOut(DeploymentSummary):
    source_type: SourceType
    repository_url: str | None
    branch: str | None
    image_name: str | None
    container_name: str | None
    internal_port: int | None
    rollback_of_id: uuid.UUID | None
    error_message: str | None
    health_check: dict[str, Any] | None
    meta: dict[str, Any]
    can_rollback: bool = False


class DeploymentPage(BaseModel):
    items: list[DeploymentSummary]
    total: int
    limit: int
    offset: int


class DeployRequest(BaseModel):
    """Optional overrides for a git deploy."""

    branch: str | None = None


class ActionAccepted(BaseModel):
    status: str = "accepted"
    detail: str
