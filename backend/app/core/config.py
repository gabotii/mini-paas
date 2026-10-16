"""Application settings, loaded from environment variables (and `.env` in development)."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal
from urllib.parse import quote

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_DEFAULT = "change-me"


class Settings(BaseSettings):
    # Secrets may come from env vars or from files in SECRETS_DIR (one file per setting, e.g.
    # /run/paas-secrets/jwt_secret). Env vars win. docker-compose generates the files once.
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,  # compose passes "" for unset overrides: fall back to secret files
        secrets_dir=os.environ.get("SECRETS_DIR") or None,
    )

    # --- general -----------------------------------------------------------------
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool = True

    # --- infrastructure ----------------------------------------------------------
    # Either a full DATABASE_URL, or the POSTGRES_* parts (password usually from a secret file).
    database_url: str = ""
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "minipaas"
    postgres_password: SecretStr = SecretStr("minipaas")
    postgres_db: str = "minipaas"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str | None = None
    docker_host: str | None = None

    # --- security ----------------------------------------------------------------
    jwt_secret: SecretStr = SecretStr(INSECURE_DEFAULT)
    jwt_issuer: str = "mini-paas"
    jwt_audience: str = "mini-paas-api"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_days: int = 14
    # Comma separated Fernet keys; the first one encrypts, all of them decrypt (rotation).
    encryption_keys: SecretStr = SecretStr("")
    internal_api_token: SecretStr = SecretStr(INSECURE_DEFAULT)
    cookie_secure: bool = False
    cors_origins: list[str] = Field(default_factory=list)

    # --- domains -----------------------------------------------------------------
    platform_host: str = "localhost"
    apps_base_domain: str = "localhost"
    public_scheme: Literal["http", "https"] = "http"
    public_port: int | None = None

    # --- storage -----------------------------------------------------------------
    data_dir: str = "/data"

    # --- limits ------------------------------------------------------------------
    max_projects_per_user: int = 10
    max_active_deployments_per_user: int = 2
    max_upload_mb: int = 100
    max_extracted_mb: int = 500
    max_archive_entries: int = 10_000
    max_compression_ratio: int = 200
    max_env_vars_per_project: int = 100
    max_env_value_bytes: int = 32_768
    app_cpu_default: float = 0.5
    app_cpu_max: float = 1.0
    app_memory_default_mb: int = 256
    app_memory_max_mb: int = 512
    app_pids_limit: int = 256
    app_tmpfs_mb: int = 64
    build_timeout_seconds: int = 900
    build_cpu_limit: float = 1.0
    build_memory_mb: int = 1024
    git_clone_timeout_seconds: int = 120
    allow_private_git_hosts: bool = False
    health_check_timeout_seconds: int = 60
    health_check_interval_seconds: float = 2.0
    route_propagation_seconds: float = 3.0
    max_log_bytes_per_deployment: int = 10 * 1024 * 1024
    max_runtime_log_lines_per_second: int = 200

    # --- retention ---------------------------------------------------------------
    log_retention_days: int = 14
    metrics_retention_hours: int = 24
    image_retention_per_project: int = 3
    workspace_max_age_minutes: int = 60
    stale_deployment_minutes: int = 20
    metrics_interval_seconds: float = 10.0

    # --- rate limits (requests per window) ----------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_login: str = "10/60"
    rate_limit_register: str = "5/3600"
    rate_limit_deploy: str = "10/60"
    rate_limit_upload: str = "10/600"
    rate_limit_ws_ticket: str = "60/60"

    # --- docker ------------------------------------------------------------------
    proxy_container_name: str = "paas-proxy"
    worker_container_name: str | None = None
    image_prefix: str = "mini-paas"

    # --- seed --------------------------------------------------------------------
    seed_demo_email: str = "demo@minipaas.local"
    seed_demo_password: SecretStr | None = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _build_database_url(self) -> Settings:
        if not self.database_url:
            password = quote(self.postgres_password.get_secret_value(), safe="")
            self.database_url = (
                f"postgresql+asyncpg://{quote(self.postgres_user, safe='')}:{password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        return self

    @model_validator(mode="after")
    def _refuse_insecure_production(self) -> Settings:
        if self.environment != "production":
            return self
        problems = []
        jwt_secret = self.jwt_secret.get_secret_value()
        if jwt_secret == INSECURE_DEFAULT or len(jwt_secret) < 32:
            problems.append("JWT_SECRET must be a random string of at least 32 characters")
        if not self.encryption_keys.get_secret_value():
            problems.append("ENCRYPTION_KEYS must be set")
        if self.internal_api_token.get_secret_value() in ("", INSECURE_DEFAULT):
            problems.append("INTERNAL_API_TOKEN must be set")
        if problems:
            raise ValueError("Insecure production configuration: " + "; ".join(problems))
        return self

    @property
    def sync_database_url(self) -> str:
        """Same database, psycopg driver — used by Celery workers, the agent and Alembic."""
        return self.database_url.replace("+asyncpg", "+psycopg")

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_extracted_bytes(self) -> int:
        return self.max_extracted_mb * 1024 * 1024

    @property
    def platform_url(self) -> str:
        port = f":{self.public_port}" if self.public_port else ""
        return f"{self.public_scheme}://{self.platform_host}{port}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
