"""Import every model so `Base.metadata` is complete (Alembic, tests)."""

from app.models.deployment import Deployment
from app.models.enums import (
    AuditAction,
    DeploymentStatus,
    DeploymentTrigger,
    FailureReason,
    LogLevel,
    LogSource,
    ProjectStatus,
    Runtime,
    SourceType,
)
from app.models.env_var import EnvironmentVariable
from app.models.observability import AuditLog, DeploymentLog, ProjectMetric
from app.models.project import Project
from app.models.user import RefreshToken, User

__all__ = [
    "AuditAction",
    "AuditLog",
    "Deployment",
    "DeploymentLog",
    "DeploymentStatus",
    "DeploymentTrigger",
    "EnvironmentVariable",
    "FailureReason",
    "LogLevel",
    "LogSource",
    "Project",
    "ProjectMetric",
    "ProjectStatus",
    "RefreshToken",
    "Runtime",
    "SourceType",
    "User",
]
