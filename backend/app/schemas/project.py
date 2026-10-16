from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import ProjectStatus, Runtime, SourceType
from app.routing.domains import SlugError, validate_slug
from app.schemas.deployment import DeploymentSummary

_BRANCH_RE = re.compile(r"^(?!-)(?!.*\.\.)[A-Za-z0-9._/-]{1,255}$")
_HEALTH_PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/?-]{0,199}$")


def _check_branch(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if not _BRANCH_RE.match(value):
        raise ValueError("Invalid branch name")
    return value


def _check_start_command(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if any(ch in value for ch in ("\n", "\r", "\x00")):
        raise ValueError("Start command must be a single line")
    return value


class ProjectSettingsFields(BaseModel):
    port: int | None = Field(default=None, ge=1, le=65535)
    start_command: str | None = Field(default=None, max_length=500)
    health_check_path: str = Field(default="/", max_length=200)
    cpu_limit: float | None = Field(default=None, gt=0, le=16)
    memory_limit_mb: int | None = Field(default=None, ge=64, le=65536)

    _start = field_validator("start_command")(_check_start_command)

    @field_validator("health_check_path")
    @classmethod
    def _health_path(cls, value: str) -> str:
        if not _HEALTH_PATH_RE.match(value):
            raise ValueError("Health check path must start with '/' and be a valid URL path")
        return value


class ProjectCreate(ProjectSettingsFields):
    name: str = Field(min_length=1, max_length=100)
    slug: str | None = Field(default=None, description="Subdomain; derived from the name if omitted")
    description: str | None = Field(default=None, max_length=500)
    runtime: Runtime = Runtime.AUTO
    source_type: SourceType
    repository_url: str | None = Field(default=None, max_length=500)
    repository_branch: str | None = Field(default=None, max_length=255)

    _branch = field_validator("repository_branch")(_check_branch)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be blank")
        return value

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        try:
            return validate_slug(value.strip().lower())
        except SlugError as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def _source(self) -> ProjectCreate:
        if self.source_type == SourceType.GIT and not self.repository_url:
            raise ValueError("repository_url is required for git projects")
        if self.source_type == SourceType.ZIP:
            self.repository_url = None
            self.repository_branch = None
        return self


class ProjectUpdate(BaseModel):
    """PATCH semantics: only fields present in the body are applied."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    runtime: Runtime | None = None
    repository_url: str | None = Field(default=None, max_length=500)
    repository_branch: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    start_command: str | None = Field(default=None, max_length=500)
    health_check_path: str | None = Field(default=None, max_length=200)
    cpu_limit: float | None = Field(default=None, gt=0, le=16)
    memory_limit_mb: int | None = Field(default=None, ge=64, le=65536)

    _branch = field_validator("repository_branch")(_check_branch)
    _start = field_validator("start_command")(_check_start_command)

    @field_validator("health_check_path")
    @classmethod
    def _health_path(cls, value: str | None) -> str | None:
        if value is not None and not _HEALTH_PATH_RE.match(value):
            raise ValueError("Health check path must start with '/' and be a valid URL path")
        return value


class MetricSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timestamp: datetime
    cpu_usage: float
    memory_usage: int
    memory_limit: int
    network_rx: int
    network_tx: int
    uptime_seconds: int
    restart_count: int


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    runtime: Runtime
    source_type: SourceType
    repository_url: str | None
    repository_branch: str | None
    has_source: bool
    status: ProjectStatus
    url: str
    port: int | None
    start_command: str | None
    health_check_path: str
    cpu_limit: float
    memory_limit_mb: int
    current_deployment: DeploymentSummary | None
    active_deployment: DeploymentSummary | None
    latest_metrics: MetricSnapshot | None = None
    created_at: datetime
    updated_at: datetime


class ProjectList(BaseModel):
    items: list[ProjectOut]
    limits: dict[str, int | float]
