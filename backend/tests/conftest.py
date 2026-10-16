"""Test configuration.

Integration tests run against a real PostgreSQL (TEST_DATABASE_URL) and Redis (TEST_REDIS_URL).
The schema is created with `alembic upgrade head` once per session (so migrations are tested
too) and every table is truncated between tests.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

from cryptography.fernet import Fernet

# --- environment must be configured before the application is imported -----------------
os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://minipaas:minipaas@127.0.0.1:5432/minipaas_test"
)
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
os.environ["JWT_SECRET"] = "test-secret-" + "x" * 40
os.environ["ENCRYPTION_KEYS"] = Fernet.generate_key().decode()
os.environ["INTERNAL_API_TOKEN"] = "test-internal-token"
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="minipaas-test-")
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["LOG_JSON"] = "false"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["HEALTH_CHECK_INTERVAL_SECONDS"] = "0"
os.environ["ROUTE_PROPAGATION_SECONDS"] = "0"

import httpx
import pytest
from sqlalchemy import text

from app.api.deps import get_task_queue
from app.core.config import get_settings
from app.core.redis import get_async_redis, get_sync_redis
from app.db.base import Base
from app.db.session import get_async_engine, get_sync_engine
from app.main import create_app


class RecordingQueue:
    """TaskQueue test double: records what the API enqueued."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fail = False

    async def _record(self, name: str, *args: Any) -> None:
        if self.fail:
            from app.core.errors import ServiceUnavailable

            raise ServiceUnavailable("queue down", code="QUEUE_UNAVAILABLE")
        self.calls.append((name, args))

    async def deploy(self, deployment_id: uuid.UUID) -> None:
        await self._record("deploy", deployment_id)

    async def stop(self, project_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await self._record("stop", project_id, user_id)

    async def restart(self, project_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await self._record("restart", project_id, user_id)

    async def delete(self, project_id: uuid.UUID) -> None:
        await self._record("delete", project_id)

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


def _migrate() -> None:
    from alembic.config import Config

    from alembic import command

    config = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    config.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    with get_sync_engine().begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def database() -> Iterator[None]:
    _migrate()
    yield
    get_sync_engine().dispose()


@pytest.fixture
def clean_db(database: None) -> None:
    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    with get_sync_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    get_sync_redis().flushdb()


@pytest.fixture
def queue() -> RecordingQueue:
    return RecordingQueue()


@pytest.fixture
async def client(clean_db: None, queue: RecordingQueue) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_task_queue] = lambda: queue
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@pytest.fixture(scope="session", autouse=True)
async def _close_async_resources() -> AsyncIterator[None]:
    yield
    await get_async_redis().aclose()
    await get_async_engine().dispose()


@pytest.fixture
def settings() -> Any:
    return get_settings()


# --- helpers ------------------------------------------------------------------------------
PASSWORD = "Sup3r-Secret-pass"


async def register(
    client: httpx.AsyncClient, email: str = "alice@example.com", name: str = "Alice"
) -> dict[str, str]:
    response = await client.post(
        "/api/auth/register", json={"email": email, "password": PASSWORD, "name": name}
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def create_project(
    client: httpx.AsyncClient, headers: dict[str, str], **overrides: Any
) -> dict[str, Any]:
    body = {"name": "My App", "source_type": "zip", **overrides}
    response = await client.post("/api/projects", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())
