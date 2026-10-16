"""Environment variables, logs, metrics, health probes and the internal Traefik endpoint."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.redis import get_sync_redis
from app.db.session import sync_session
from app.logs.sink import LogSink
from app.models import (
    AuditLog,
    Deployment,
    DeploymentStatus,
    EnvironmentVariable,
    LogLevel,
    LogSource,
    Project,
    ProjectMetric,
    ProjectStatus,
)
from tests.conftest import create_project, register
from tests.integration.test_deployments_api import finish, upload

pytestmark = pytest.mark.integration


# ------------------------------------------------------------------ environment
async def test_env_crud_masks_and_encrypts(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    project = await create_project(client, headers)
    base = f"/api/projects/{project['id']}/env"

    created = await client.post(base, json={"key": "SECRET_KEY", "value": "hunter2"}, headers=headers)
    assert created.status_code == 201
    assert created.json()["value"] == "••••••••"
    plain = await client.post(
        base, json={"key": "NODE_ENV", "value": "production", "is_secret": False}, headers=headers
    )
    assert plain.json()["value"] == "production"

    listing = (await client.get(base, headers=headers)).json()
    assert [(v["key"], v["value"]) for v in listing] == [
        ("NODE_ENV", "production"),
        ("SECRET_KEY", "••••••••"),
    ]
    assert "hunter2" not in str(listing)

    with sync_session() as session:
        stored = session.scalar(select(EnvironmentVariable).where(EnvironmentVariable.key == "SECRET_KEY"))
        assert stored is not None
        assert "hunter2" not in stored.encrypted_value

    revealed = await client.post(f"{base}/SECRET_KEY/reveal", headers=headers)
    assert revealed.json() == {"key": "SECRET_KEY", "value": "hunter2"}

    updated = await client.patch(f"{base}/SECRET_KEY", json={"value": "new-value"}, headers=headers)
    assert updated.status_code == 200
    assert (await client.post(f"{base}/SECRET_KEY/reveal", headers=headers)).json()["value"] == "new-value"

    assert (await client.delete(f"{base}/SECRET_KEY", headers=headers)).status_code == 204
    assert (await client.delete(f"{base}/SECRET_KEY", headers=headers)).status_code == 404

    with sync_session() as session:
        actions = list(session.scalars(select(AuditLog.action).order_by(AuditLog.id)))
        details = [d for d in session.scalars(select(AuditLog.details)) if d.get("key")]
    assert actions.count("ENV_UPDATED") == 4
    assert actions.count("ENV_REVEALED") == 2
    assert all("hunter2" not in str(d) and "new-value" not in str(d) for d in details)  # values never audited


async def test_env_validation(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    project = await create_project(client, headers)
    base = f"/api/projects/{project['id']}/env"
    for key in ("1ABC", "WITH-DASH", "SPACE KEY", ""):
        assert (
            await client.post(base, json={"key": key, "value": "x"}, headers=headers)
        ).status_code == 422, key
    reserved = await client.post(base, json={"key": "MINIPAAS_PROJECT", "value": "x"}, headers=headers)
    assert reserved.status_code == 422
    await client.post(base, json={"key": "PORT", "value": "8000"}, headers=headers)
    dup = await client.post(base, json={"key": "PORT", "value": "9000"}, headers=headers)
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "ENV_EXISTS"
    huge = await client.post(base, json={"key": "BIG", "value": "x" * 40_000}, headers=headers)
    assert huge.status_code == 422


# ------------------------------------------------------------------ logs
def write_logs(deployment_id: uuid.UUID, lines: list[tuple[str, LogSource, LogLevel]]) -> None:
    with LogSink(sync_session, get_sync_redis(), deployment_id, max_bytes=10_000, background=False) as sink:
        for message, source, level in lines:
            sink.write(message, source=source, level=level)


async def test_logs_endpoints(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    project = await create_project(client, headers)
    deployment = (await upload(client, headers, project["id"])).json()
    deployment_id = uuid.UUID(deployment["id"])
    write_logs(
        deployment_id,
        [
            ("Detecting runtime...", LogSource.BUILD, LogLevel.INFO),
            ("Building image...", LogSource.BUILD, LogLevel.INFO),
            ("Health check passed", LogSource.DEPLOY, LogLevel.INFO),
            ("Traceback: boom", LogSource.RUNTIME, LogLevel.ERROR),
        ],
    )
    page = (await client.get(f"/api/deployments/{deployment_id}/logs", headers=headers)).json()
    assert [line["message"] for line in page["items"]][:2] == ["Detecting runtime...", "Building image..."]
    after = page["items"][1]["id"]
    rest = (
        await client.get(
            f"/api/deployments/{deployment_id}/logs", params={"after_id": after}, headers=headers
        )
    ).json()
    assert [line["message"] for line in rest["items"]] == ["Health check passed", "Traceback: boom"]
    errors = (
        await client.get(f"/api/deployments/{deployment_id}/logs", params={"level": "ERROR"}, headers=headers)
    ).json()
    assert [line["message"] for line in errors["items"]] == ["Traceback: boom"]
    tail = (
        await client.get(f"/api/projects/{project['id']}/logs", params={"limit": 1}, headers=headers)
    ).json()
    assert tail["deployment_id"] == str(deployment_id)
    assert [line["message"] for line in tail["items"]] == ["Traceback: boom"]


async def test_log_sink_enforces_byte_budget(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    project = await create_project(client, headers)
    deployment_id = uuid.UUID((await upload(client, headers, project["id"])).json()["id"])
    with LogSink(sync_session, get_sync_redis(), deployment_id, max_bytes=100, background=False) as sink:
        for i in range(20):
            sink.write(f"line {i:02d} " + "x" * 10)
    with sync_session() as session:
        used = session.scalar(select(Deployment.log_bytes).where(Deployment.id == deployment_id))
    page = (await client.get(f"/api/deployments/{deployment_id}/logs", headers=headers)).json()["items"]
    assert len(page) < 20
    assert page[-1]["message"].startswith("Log size limit reached")
    assert used is not None and used <= 100 + 100  # the budget plus the single notice line


# ------------------------------------------------------------------ metrics
async def test_metrics_endpoint(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    project = await create_project(client, headers)
    deployment = (await upload(client, headers, project["id"])).json()
    finish(deployment["id"])
    now = datetime.now(UTC)
    with sync_session() as session:
        for i, cpu in enumerate([5.0, 12.5]):
            session.add(
                ProjectMetric(
                    project_id=uuid.UUID(project["id"]), deployment_id=uuid.UUID(deployment["id"]),
                    timestamp=now - timedelta(seconds=10 * (1 - i)), cpu_usage=cpu, memory_usage=200 * 2**20,
                    memory_limit=256 * 2**20, network_rx=1000 * (i + 1), network_tx=500, uptime_seconds=60 + i * 10,
                    restart_count=0,
                )
            )  # fmt: skip
        session.add(  # outside the window
            ProjectMetric(project_id=uuid.UUID(project["id"]), timestamp=now - timedelta(hours=3), cpu_usage=99,
                          memory_usage=1, memory_limit=1, network_rx=0, network_tx=0, uptime_seconds=0, restart_count=0)
        )  # fmt: skip
    body = (
        await client.get(f"/api/projects/{project['id']}/metrics", params={"window": 60}, headers=headers)
    ).json()
    assert [s["cpu_usage"] for s in body["samples"]] == [5.0, 12.5]
    assert body["current"]["cpu_usage"] == 12.5
    assert body["current"]["memory_usage"] == 200 * 2**20

    dashboard = (await client.get("/api/projects", headers=headers)).json()["items"][0]
    assert dashboard["latest_metrics"]["cpu_usage"] == 12.5
    assert (
        await client.get(f"/api/projects/{project['id']}/metrics", params={"window": 2}, headers=headers)
    ).status_code == 422


# ------------------------------------------------------------------ probes & proxy config
async def test_health_and_ready(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).json() == {"status": "ok"}
    ready = await client.get("/ready")
    assert ready.status_code == 200
    checks = ready.json()["checks"]
    assert checks["database"]["status"] == "ok"
    assert checks["redis"]["status"] == "ok"
    assert ready.headers["x-request-id"]
    assert ready.headers["x-content-type-options"] == "nosniff"


async def test_request_id_is_propagated(client: httpx.AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
    assert response.headers["x-request-id"] == "trace-abc-123"
    unsafe = await client.get("/health", headers={"X-Request-ID": "bad id with spaces\n"})
    assert unsafe.headers["x-request-id"] != "bad id with spaces\n"


async def test_errors_never_leak_internals(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert set(response.json()["error"]) == {"code", "message", "details"}


async def test_traefik_config_endpoint(client: httpx.AsyncClient) -> None:
    assert (await client.get("/internal/traefik/config")).status_code == 404
    assert (await client.get("/internal/traefik/config", params={"token": "wrong"})).status_code == 404

    headers = await register(client)
    project = await create_project(client, headers, name="Routed App")
    stopped = await create_project(client, headers, name="Stopped App")
    deployment = (await upload(client, headers, project["id"])).json()
    finish(deployment["id"])
    other = (await upload(client, headers, stopped["id"])).json()
    finish(other["id"])
    with sync_session() as session:
        target = session.get(Project, uuid.UUID(stopped["id"]))
        assert target is not None
        target.status = ProjectStatus.STOPPED
        assert (
            session.scalar(select(Deployment.status).where(Deployment.id == uuid.UUID(deployment["id"])))
            == DeploymentStatus.RUNNING
        )

    config = (await client.get("/internal/traefik/config", params={"token": "test-internal-token"})).json()
    routers = config["http"]["routers"]
    assert list(routers) == ["app-routed-app"]
    assert routers["app-routed-app"]["rule"] == "Host(`routed-app.localhost`)"
    url = config["http"]["services"]["app-routed-app"]["loadBalancer"]["servers"][0]["url"]
    assert url == f"http://d-{uuid.UUID(deployment['id']).hex[:16]}:8080"


async def test_openapi_documents_all_groups(client: httpx.AsyncClient) -> None:
    spec = (await client.get("/api/openapi.json")).json()
    tags = {tag for path in spec["paths"].values() for op in path.values() for tag in op.get("tags", [])}
    assert {
        "auth",
        "projects",
        "deployments",
        "lifecycle",
        "logs",
        "environment",
        "metrics",
        "health",
    } <= tags
    assert "/internal/traefik/config" not in spec["paths"]
    assert (
        "application/zip"
        in spec["paths"]["/api/projects/{project_id}/upload"]["post"]["requestBody"]["content"]
    )
