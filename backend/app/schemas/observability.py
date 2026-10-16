from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import LogLevel, LogSource
from app.schemas.project import MetricSnapshot


class LogLine(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    deployment_id: uuid.UUID
    timestamp: datetime
    level: LogLevel
    source: LogSource
    message: str


class LogPage(BaseModel):
    deployment_id: uuid.UUID | None
    items: list[LogLine]
    next_after_id: int | None


class MetricsSummary(BaseModel):
    cpu_usage: float
    memory_usage: int
    memory_limit: int
    network_rx: int
    network_tx: int
    uptime_seconds: int
    restart_count: int


class MetricsResponse(BaseModel):
    project_id: uuid.UUID
    window_minutes: int
    current: MetricsSummary | None
    samples: list[MetricSnapshot]


class AuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    resource_type: str | None
    resource_id: str | None
    ip: str | None
    details: dict[str, object]
    created_at: datetime
