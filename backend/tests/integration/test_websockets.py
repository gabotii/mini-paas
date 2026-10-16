"""WebSocket log streaming and user events (Starlette TestClient runs the app in its own loop)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.deps import get_task_queue
from app.core.redis import get_async_redis, get_sync_redis
from app.db.session import get_async_engine, get_async_sessionmaker, sync_session
from app.deployments import events
from app.logs.sink import LogSink
from app.main import create_app
from app.models import LogSource
from tests.conftest import PASSWORD, RecordingQueue
from tests.helpers import STATIC_SITE, make_zip

pytestmark = pytest.mark.integration


@pytest.fixture
def sync_client(clean_db: None) -> Iterator[TestClient]:
    # The TestClient runs its own event loop: give it fresh async engine/redis instances.
    for cached in (get_async_engine, get_async_sessionmaker, get_async_redis):
        cached.cache_clear()
    app = create_app()
    app.dependency_overrides[get_task_queue] = RecordingQueue
    with TestClient(app) as client:
        yield client
    for cached in (get_async_engine, get_async_sessionmaker, get_async_redis):
        cached.cache_clear()


def login(client: TestClient, email: str = "ws@example.com") -> dict[str, str]:
    response = client.post("/api/auth/register", json={"email": email, "password": PASSWORD, "name": "WS"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def ticket(client: TestClient, headers: dict[str, str]) -> str:
    return str(client.post("/api/auth/ws-ticket", headers=headers).json()["ticket"])


def deploy(client: TestClient, headers: dict[str, str]) -> str:
    project = client.post(
        "/api/projects", json={"name": "ws app", "source_type": "zip"}, headers=headers
    ).json()
    response = client.post(
        f"/api/projects/{project['id']}/upload",
        content=make_zip(STATIC_SITE),
        headers={**headers, "Content-Type": "application/zip"},
    )
    return str(response.json()["id"])


def test_logs_stream_backlog_then_live(sync_client: TestClient) -> None:
    headers = login(sync_client)
    deployment_id = deploy(sync_client, headers)
    with LogSink(
        sync_session, get_sync_redis(), uuid.UUID(deployment_id), max_bytes=10_000, background=False
    ) as sink:
        sink.write("old line 1", source=LogSource.BUILD)
        sink.write("old line 2", source=LogSource.BUILD)

    with sync_client.websocket_connect(
        f"/ws/deployments/{deployment_id}/logs?ticket={ticket(sync_client, headers)}"
    ) as ws:
        backlog = ws.receive_json()
        assert backlog["type"] == "backlog"
        assert [line["message"] for line in backlog["data"]["lines"]] == ["old line 1", "old line 2"]
        with LogSink(
            sync_session, get_sync_redis(), uuid.UUID(deployment_id), max_bytes=10_000, background=False
        ) as sink:
            sink.write("Health check passed.", source=LogSource.DEPLOY)
        live = ws.receive_json()
        assert live["type"] == "logs"
        assert [line["message"] for line in live["data"]["lines"]] == ["Health check passed."]


def test_ticket_is_single_use_and_required(sync_client: TestClient) -> None:
    headers = login(sync_client)
    deployment_id = deploy(sync_client, headers)
    single = ticket(sync_client, headers)
    with sync_client.websocket_connect(f"/ws/deployments/{deployment_id}/logs?ticket={single}") as ws:
        assert ws.receive_json()["type"] == "backlog"
    for bad in (single, "forged-ticket", ""):
        with (
            pytest.raises(WebSocketDisconnect) as exc,
            sync_client.websocket_connect(f"/ws/deployments/{deployment_id}/logs?ticket={bad}") as ws,
        ):
            ws.receive_json()
        assert exc.value.code == 4401


def test_cannot_stream_other_users_logs(sync_client: TestClient) -> None:
    owner = login(sync_client, "owner@example.com")
    deployment_id = deploy(sync_client, owner)
    intruder = login(sync_client, "intruder@example.com")
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        sync_client.websocket_connect(
            f"/ws/deployments/{deployment_id}/logs?ticket={ticket(sync_client, intruder)}"
        ) as ws,
    ):
        ws.receive_json()
    assert exc.value.code == 4404


def test_user_events_channel(sync_client: TestClient) -> None:
    headers = login(sync_client)
    me = sync_client.get("/api/auth/me", headers=headers).json()
    with sync_client.websocket_connect(f"/ws/events?ticket={ticket(sync_client, headers)}") as ws:
        assert ws.receive_json()["type"] == "hello"
        events.publish(
            get_sync_redis(),
            events.user_channel(me["id"]),
            "project.updated",
            {"id": "p1", "status": "RUNNING"},
        )
        message = ws.receive_json()
        assert message == {"type": "project.updated", "data": {"id": "p1", "status": "RUNNING"}}
