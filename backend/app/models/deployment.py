from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey, utcnow
from app.db.types import str_enum
from app.models.enums import (
    DeploymentStatus,
    DeploymentTrigger,
    FailureReason,
    Runtime,
    SourceType,
)

_ACTIVE = ", ".join(f"'{s.value}'" for s in DeploymentStatus.active())


class Deployment(UUIDPrimaryKey, Base):
    __tablename__ = "deployments"
    __table_args__ = (
        UniqueConstraint("project_id", "number"),
        # The concurrency guarantee: at most one in-progress deployment per project.
        Index(
            "uq_deployments_one_active_per_project",
            "project_id",
            unique=True,
            postgresql_where=text(f"status IN ({_ACTIVE})"),
        ),
        Index("ix_deployments_project_id_created_at", "project_id", "created_at"),
        Index("ix_deployments_status", "status"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[DeploymentStatus] = mapped_column(
        str_enum(DeploymentStatus, "deployment_status"),
        default=DeploymentStatus.QUEUED,
        nullable=False,
    )
    trigger: Mapped[DeploymentTrigger] = mapped_column(
        str_enum(DeploymentTrigger, "deployment_trigger"), nullable=False
    )
    source_type: Mapped[SourceType] = mapped_column(str_enum(SourceType, "source_type"), nullable=False)
    source_archive: Mapped[str | None] = mapped_column(String(500))
    repository_url: Mapped[str | None] = mapped_column(String(500))
    branch: Mapped[str | None] = mapped_column(String(255))
    commit_hash: Mapped[str | None] = mapped_column(String(64))
    runtime: Mapped[Runtime | None] = mapped_column(str_enum(Runtime, "runtime"))
    image_name: Mapped[str | None] = mapped_column(String(255))
    container_id: Mapped[str | None] = mapped_column(String(80))
    container_name: Mapped[str | None] = mapped_column(String(128))
    internal_port: Mapped[int | None] = mapped_column(Integer)
    rollback_of_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("deployments.id", ondelete="SET NULL")
    )
    failure_reason: Mapped[FailureReason | None] = mapped_column(str_enum(FailureReason, "failure_reason"))
    error_message: Mapped[str | None] = mapped_column(Text)
    health_check: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    log_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def succeeded(self) -> bool:
        """True when this deployment reached RUNNING at some point."""
        return self.deployed_at is not None

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.finished_at
        if end is None:
            return None
        return round((end - self.started_at).total_seconds(), 1)
