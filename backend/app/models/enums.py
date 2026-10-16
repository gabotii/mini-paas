from __future__ import annotations

from enum import StrEnum


class Runtime(StrEnum):
    AUTO = "auto"
    PYTHON = "python"
    NODE = "node"
    STATIC = "static"
    DOTNET = "dotnet"
    DOCKERFILE = "dockerfile"


class SourceType(StrEnum):
    ZIP = "zip"
    GIT = "git"


class ProjectStatus(StrEnum):
    NEW = "NEW"  # never deployed
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"  # last deployment failed and nothing is serving
    CRASHED = "CRASHED"  # the serving container died
    DELETING = "DELETING"


class DeploymentStatus(StrEnum):
    QUEUED = "QUEUED"
    BUILDING = "BUILDING"
    DEPLOYING = "DEPLOYING"
    HEALTH_CHECKING = "HEALTH_CHECKING"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @classmethod
    def active(cls) -> tuple[DeploymentStatus, ...]:
        """States in which a deployment pipeline is still in progress."""
        return (cls.QUEUED, cls.BUILDING, cls.DEPLOYING, cls.HEALTH_CHECKING)

    @classmethod
    def terminal(cls) -> tuple[DeploymentStatus, ...]:
        return (cls.STOPPED, cls.FAILED, cls.CANCELLED)


class FailureReason(StrEnum):
    BUILD_FAILED = "BUILD_FAILED"
    DEPLOY_FAILED = "DEPLOY_FAILED"
    HEALTH_CHECK_FAILED = "HEALTH_CHECK_FAILED"
    RUNTIME_CRASHED = "RUNTIME_CRASHED"
    TIMEOUT = "TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class DeploymentTrigger(StrEnum):
    DEPLOY = "deploy"
    UPLOAD = "upload"
    ROLLBACK = "rollback"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class LogSource(StrEnum):
    BUILD = "build"
    DEPLOY = "deploy"
    RUNTIME = "runtime"
    SYSTEM = "system"


class AuditAction(StrEnum):
    USER_REGISTERED = "USER_REGISTERED"
    USER_LOGGED_IN = "USER_LOGGED_IN"
    USER_LOGIN_FAILED = "USER_LOGIN_FAILED"
    USER_LOGGED_OUT = "USER_LOGGED_OUT"
    REFRESH_TOKEN_REUSE = "REFRESH_TOKEN_REUSE"  # noqa: S105 — an action name
    PROJECT_CREATED = "PROJECT_CREATED"
    PROJECT_UPDATED = "PROJECT_UPDATED"
    PROJECT_DELETED = "PROJECT_DELETED"
    SOURCE_UPLOADED = "SOURCE_UPLOADED"
    DEPLOYMENT_STARTED = "DEPLOYMENT_STARTED"
    DEPLOYMENT_SUCCEEDED = "DEPLOYMENT_SUCCEEDED"
    DEPLOYMENT_FAILED = "DEPLOYMENT_FAILED"
    DEPLOYMENT_CANCELLED = "DEPLOYMENT_CANCELLED"
    ROLLBACK_STARTED = "ROLLBACK_STARTED"
    PROJECT_STOPPED = "PROJECT_STOPPED"
    PROJECT_RESTARTED = "PROJECT_RESTARTED"
    ENV_UPDATED = "ENV_UPDATED"
    ENV_REVEALED = "ENV_REVEALED"
