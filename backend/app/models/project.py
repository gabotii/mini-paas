from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import str_enum
from app.models.enums import ProjectStatus, Runtime, SourceType

if TYPE_CHECKING:
    from app.models.deployment import Deployment


class Project(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_user_id_created_at", "user_id", "created_at"),
        CheckConstraint("cpu_limit > 0 AND cpu_limit <= 16", name="cpu_limit_range"),
        CheckConstraint("memory_limit_mb >= 32 AND memory_limit_mb <= 65536", name="memory_range"),
        CheckConstraint("port IS NULL OR (port >= 1 AND port <= 65535)", name="port_range"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    runtime: Mapped[Runtime] = mapped_column(str_enum(Runtime, "runtime"), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(str_enum(SourceType, "source_type"), nullable=False)
    repository_url: Mapped[str | None] = mapped_column(String(500))
    repository_branch: Mapped[str | None] = mapped_column(String(255))
    # Path (relative to DATA_DIR) of the most recently uploaded archive, used for redeploys.
    source_archive: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[ProjectStatus] = mapped_column(
        str_enum(ProjectStatus, "project_status"), default=ProjectStatus.NEW, nullable=False
    )
    port: Mapped[int | None] = mapped_column(Integer)
    start_command: Mapped[str | None] = mapped_column(String(500))
    health_check_path: Mapped[str] = mapped_column(String(200), default="/", nullable=False)
    cpu_limit: Mapped[float] = mapped_column(Float, nullable=False)
    memory_limit_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    deployment_counter: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    current_deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("deployments.id", ondelete="SET NULL", use_alter=True),
    )

    current_deployment: Mapped[Deployment | None] = relationship(
        foreign_keys=[current_deployment_id], lazy="raise", post_update=True
    )
