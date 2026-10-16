"""Append-heavy tables: deployment logs, metrics samples and the audit log."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow
from app.db.types import str_enum
from app.models.enums import LogLevel, LogSource


class DeploymentLog(Base):
    __tablename__ = "deployment_logs"
    __table_args__ = (
        Index("ix_deployment_logs_deployment_id_id", "deployment_id", "id"),
        Index("ix_deployment_logs_timestamp", "timestamp"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("deployments.id", ondelete="CASCADE"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    level: Mapped[LogLevel] = mapped_column(str_enum(LogLevel, "log_level"), nullable=False)
    source: Mapped[LogSource] = mapped_column(str_enum(LogSource, "log_source"), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)


class ProjectMetric(Base):
    __tablename__ = "project_metrics"
    __table_args__ = (Index("ix_project_metrics_project_id_timestamp", "project_id", "timestamp"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("deployments.id", ondelete="SET NULL")
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    cpu_usage: Mapped[float] = mapped_column(Float, nullable=False)  # percent of one core
    memory_usage: Mapped[int] = mapped_column(BigInteger, nullable=False)  # bytes
    memory_limit: Mapped[int] = mapped_column(BigInteger, nullable=False)  # bytes
    network_rx: Mapped[int] = mapped_column(BigInteger, nullable=False)  # cumulative bytes
    network_tx: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uptime_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    restart_count: Mapped[int] = mapped_column(Integer, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_user_id_created_at", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
