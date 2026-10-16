"""Worker-side behaviour against the real database with an in-memory Docker double:
the deployment pipeline, lifecycle operations, maintenance and the runtime agent."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update

from app.agent.runner import Agent
from app.builds.build_service import BuildService
from app.builds.workspace import Storage
from app.core.config import get_settings
from app.core.crypto import get_secret_box
from app.core.redis import get_sync_redis
from app.db.session import sync_session
from app.deployments.naming import network_name
from app.models import (
    Deployment,
    DeploymentLog,
    DeploymentStatus,
    EnvironmentVariable,
    FailureReason,
    LogSource,
    Project,
    ProjectMetric,
    ProjectStatus,
)
from app.workers.lifecycle import LifecycleService
from app.workers.maintenance import MaintenanceService
from app.workers.pipeline import DeploymentPipeline
from app.workers.store import DeploymentStore
from tests.conftest import create_project, register
from tests.fakes import FakeDocker
from tests.helpers import make_zip

pytestmark = pytest.mark.integration

PYTHON_APP = {
    "requirements.txt": "fastapi\nuvicorn\n",
    "main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
}


class Harness:
    def __init__(self, probe_status: int = 200) -> None:
        self.settings = get_settings()
        self.docker = FakeDocker()
        self.storage = Storage(self.settings.data_dir)
        self.store = DeploymentStore(sync_session, get_sync_redis(), get_secret_box())
        self.probe_status = probe_status
        self.probes: list[str] = []
        self.pipeline = DeploymentPipeline(
            settings=self.settings,
            docker=self.docker,  # type: ignore[arg-type]
            builds=BuildService(self.settings, self.docker, self.storage),  # type: ignore[arg-type]
            store=self.store,
            session_factory=sync_session,
            redis_client=get_sync_redis(),
            probe=self._probe,
            sleep=lambda _: None,
        )
        self.lifecycle = LifecycleService(
            self.settings, self.docker, self.store, self.storage, sync_session, get_sync_redis(),  # type: ignore[arg-type]
            sleep=lambda _: None,
        )  # fmt: skip

    def _probe(self, url: str, timeout: float) -> int:
        self.probes.append(url)
        return self.probe_status

    def run(self, deployment_id: str) -> DeploymentStatus | None:
        return self.pipeline.run(uuid.UUID(deployment_id))


def load(deployment_id: str) -> Deployment:
    with sync_session() as session:
        deployment = session.get(Deployment, uuid.UUID(deployment_id))
        assert deployment is not None
        return deployment


def load_project(project_id: str) -> Project:
    with sync_session() as session:
        project = session.get(Project, uuid.UUID(project_id))
        assert project is not None
        return project


def log_messages(deployment_id: str) -> list[str]:
    with sync_session() as session:
        return list(
            session.scalars(
                select(DeploymentLog.message)
                .where(DeploymentLog.deployment_id == uuid.UUID(deployment_id))
                .order_by(DeploymentLog.id)
            )
        )


async def start(
    client: httpx.AsyncClient, files: dict[str, Any] | None = None, **project: Any
) -> tuple[dict[str, str], dict[str, Any], dict[str, Any]]:
    headers = await register(client)
    created = await create_project(client, headers, **project)
    response = await client.post(
        f"/api/projects/{created['id']}/upload",
        content=make_zip(files or PYTHON_APP),
        headers={**headers, "Content-Type": "application/zip"},
    )
    assert response.status_code == 202, response.text
    return headers, created, response.json()


async def test_successful_python_deployment(client: httpx.AsyncClient) -> None:
    headers, project, deployment = await start(client)
    await client.post(
        f"/api/projects/{project['id']}/env", json={"key": "GREETING", "value": "hola"}, headers=headers
    )
    h = Harness()

    assert h.run(deployment["id"]) == DeploymentStatus.RUNNING

    stored = load(deployment["id"])
    assert stored.status == DeploymentStatus.RUNNING
    assert stored.runtime == "python"
    assert stored.image_name == f"mini-paas/project-{project['id']}:deployment-1"
    assert stored.internal_port == 8000
    assert stored.health_check and stored.health_check["ok"] is True
    assert stored.duration_seconds is not None
    assert stored.meta["start_command"].startswith("uvicorn main:app")

    spec = next(iter(h.docker.containers.values()))["spec"]
    assert spec.environment["GREETING"] == "hola"  # decrypted only inside the worker
    assert spec.environment["PORT"] == "8000"
    assert spec.read_only is True
    assert spec.network == network_name(uuid.UUID(project["id"]))
    assert (get_settings().proxy_container_name, spec.network) in h.docker.attachments
    assert ("worker", spec.network) not in h.docker.attachments  # detached after the health check
    assert h.probes[0] == f"http://{spec.alias}:8000/"

    current = load_project(project["id"])
    assert current.status == ProjectStatus.RUNNING
    assert str(current.current_deployment_id) == deployment["id"]

    messages = log_messages(deployment["id"])
    for expected in ("$ deployment started", "Runtime: python (requirements.txt found)", "Building Docker image...",
                     "#1 [internal] load build definition", "Container started.", "Deployment successful."):  # fmt: skip
        assert expected in messages
    assert not Path(h.storage.workspace(uuid.UUID(deployment["id"]))).exists()  # workspace cleaned up

    config = (await client.get("/internal/traefik/config", params={"token": "test-internal-token"})).json()
    assert f"app-{project['slug']}" in config["http"]["routers"]


async def test_build_failure(client: httpx.AsyncClient) -> None:
    _, project, deployment = await start(client)
    h = Harness()
    h.docker.fail_build = "docker build exited with code 1: ERROR: failed to solve"
    assert h.run(deployment["id"]) == DeploymentStatus.FAILED
    stored = load(deployment["id"])
    assert stored.failure_reason == FailureReason.BUILD_FAILED
    assert "failed to solve" in (stored.error_message or "")
    assert load_project(project["id"]).status == ProjectStatus.FAILED
    assert not h.docker.containers


async def test_undetectable_runtime_fails_build(client: httpx.AsyncClient) -> None:
    _, _, deployment = await start(client, {"README.md": "nothing to run"})
    h = Harness()
    assert h.run(deployment["id"]) == DeploymentStatus.FAILED
    stored = load(deployment["id"])
    assert stored.failure_reason == FailureReason.BUILD_FAILED
    assert "Could not detect the runtime" in (stored.error_message or "")
    assert not h.docker.events_log  # never reached Docker


async def test_health_check_failure_keeps_previous_version(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "health_check_timeout_seconds", 1)
    headers, project, first = await start(client)
    h = Harness()
    assert h.run(first["id"]) == DeploymentStatus.RUNNING
    first_container = load(first["id"]).container_id

    second = (await client.post(f"/api/projects/{project['id']}/deploy", headers=headers)).json()
    h.probe_status = 500
    assert h.run(second["id"]) == DeploymentStatus.FAILED

    failed = load(second["id"])
    assert failed.failure_reason == FailureReason.HEALTH_CHECK_FAILED
    assert failed.health_check and failed.health_check["status_code"] == 500
    assert failed.container_id not in h.docker.containers  # discarded
    assert first_container in h.docker.containers  # old version untouched
    current = load_project(project["id"])
    assert current.status == ProjectStatus.RUNNING
    assert str(current.current_deployment_id) == first["id"]
    assert "Last application output:" in log_messages(second["id"])


async def test_container_exit_during_health_check(client: httpx.AsyncClient) -> None:
    _, _, deployment = await start(client)
    h = Harness()
    h.docker.exit_on_start = 1
    assert h.run(deployment["id"]) == DeploymentStatus.FAILED
    stored = load(deployment["id"])
    assert stored.failure_reason == FailureReason.HEALTH_CHECK_FAILED
    assert stored.error_message == "Container exited with code 1"


async def test_new_deployment_supersedes_old_one(client: httpx.AsyncClient) -> None:
    headers, project, first = await start(client)
    h = Harness()
    h.run(first["id"])
    old_container = load(first["id"]).container_id
    second = (await client.post(f"/api/projects/{project['id']}/deploy", headers=headers)).json()
    assert h.run(second["id"]) == DeploymentStatus.RUNNING

    superseded = load(first["id"])
    assert superseded.status == DeploymentStatus.STOPPED
    assert superseded.meta["superseded_by"] == 2
    assert old_container not in h.docker.containers
    assert str(load_project(project["id"]).current_deployment_id) == second["id"]


async def test_rollback_reuses_image(client: httpx.AsyncClient) -> None:
    headers, project, first = await start(client)
    h = Harness()
    h.run(first["id"])
    second = (await client.post(f"/api/projects/{project['id']}/deploy", headers=headers)).json()
    h.run(second["id"])
    builds_before = [e for e in h.docker.events_log if e.startswith("build:")]

    rollback = (await client.post(f"/api/deployments/{first['id']}/rollback", headers=headers)).json()
    assert h.run(rollback["id"]) == DeploymentStatus.RUNNING

    assert [e for e in h.docker.events_log if e.startswith("build:")] == builds_before  # no rebuild
    restored = load(rollback["id"])
    assert restored.image_name == load(first["id"]).image_name
    assert restored.runtime == "python"
    assert "Rolling back: reusing image" in " ".join(log_messages(rollback["id"]))


async def test_rollback_fails_when_image_is_gone(client: httpx.AsyncClient) -> None:
    headers, project, first = await start(client)
    h = Harness()
    h.run(first["id"])
    second = (await client.post(f"/api/projects/{project['id']}/deploy", headers=headers)).json()
    h.run(second["id"])
    rollback = (await client.post(f"/api/deployments/{first['id']}/rollback", headers=headers)).json()
    h.docker.images.clear()
    assert h.run(rollback["id"]) == DeploymentStatus.FAILED
    assert load(rollback["id"]).failure_reason == FailureReason.DEPLOY_FAILED


async def test_cancelled_before_start_is_skipped(client: httpx.AsyncClient) -> None:
    headers, _, deployment = await start(client)
    await client.post(f"/api/deployments/{deployment['id']}/cancel", headers=headers)
    h = Harness()
    assert h.run(deployment["id"]) is None
    assert load(deployment["id"]).status == DeploymentStatus.CANCELLED
    assert not h.docker.events_log


async def test_cancel_requested_during_build(client: httpx.AsyncClient) -> None:
    _, _, deployment = await start(client)
    with sync_session() as session:
        session.execute(update(Deployment).values(cancel_requested=True))
    h = Harness()
    assert h.run(deployment["id"]) == DeploymentStatus.CANCELLED
    assert not h.docker.containers


async def test_stop_restart_and_delete(client: httpx.AsyncClient) -> None:
    headers, project, deployment = await start(client)
    user_id = uuid.UUID((await client.get("/api/auth/me", headers=headers)).json()["id"])
    h = Harness()
    h.run(deployment["id"])
    container = load(deployment["id"]).container_id
    assert container

    assert h.lifecycle.restart(uuid.UUID(project["id"]), user_id) is True
    assert f"restart:{container}" in h.docker.events_log

    h.lifecycle.stop(uuid.UUID(project["id"]), user_id)
    assert load(deployment["id"]).status == DeploymentStatus.STOPPED
    assert load_project(project["id"]).status == ProjectStatus.STOPPED
    assert container not in h.docker.containers
    assert network_name(uuid.UUID(project["id"])) not in h.docker.networks
    assert "Application stopped by user." in log_messages(deployment["id"])
    assert h.lifecycle.restart(uuid.UUID(project["id"]), user_id) is False

    h.lifecycle.delete(uuid.UUID(project["id"]))
    with sync_session() as session:
        assert session.get(Project, uuid.UUID(project["id"])) is None
        assert session.scalar(select(Deployment)) is None  # cascaded
    assert not h.docker.list_project_images(project["id"])
    assert not h.storage.project_sources(uuid.UUID(project["id"])).exists()


async def test_reaper_fails_stale_deployments(client: httpx.AsyncClient) -> None:
    _, _, deployment = await start(client)
    h = Harness()
    maintenance = MaintenanceService(h.settings, sync_session, h.store, h.docker, h.storage)  # type: ignore[arg-type]
    assert maintenance.reap_stale_deployments() == 0
    future = datetime.now(UTC) + timedelta(minutes=h.settings.stale_deployment_minutes + 1)
    assert maintenance.reap_stale_deployments(now=future) == 1
    stored = load(deployment["id"])
    assert stored.status == DeploymentStatus.FAILED
    assert stored.failure_reason == FailureReason.TIMEOUT


async def test_cleanup_keeps_images_needed_for_rollback(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "image_retention_per_project", 2)
    headers, project, first = await start(client)
    h = Harness()
    h.run(first["id"])
    ids = [first["id"]]
    for _ in range(3):
        nxt = (await client.post(f"/api/projects/{project['id']}/deploy", headers=headers)).json()
        h.run(nxt["id"])
        ids.append(nxt["id"])
    with sync_session() as session:  # an old log line and an old metric sample
        session.add(DeploymentLog(deployment_id=uuid.UUID(ids[0]), level="INFO", source=LogSource.SYSTEM,
                                  message="ancient", timestamp=datetime.now(UTC) - timedelta(days=60)))  # fmt: skip
        session.add(ProjectMetric(project_id=uuid.UUID(project["id"]), timestamp=datetime.now(UTC) - timedelta(days=2),
                                  cpu_usage=1, memory_usage=1, memory_limit=1, network_rx=0, network_tx=0,
                                  uptime_seconds=1, restart_count=0))  # fmt: skip

    maintenance = MaintenanceService(h.settings, sync_session, h.store, h.docker, h.storage)  # type: ignore[arg-type]
    report = maintenance.cleanup()

    images = [load(i).image_name for i in ids]
    assert report.images == 2
    assert images[0] not in h.docker.images and images[1] not in h.docker.images
    assert images[2] in h.docker.images and images[3] in h.docker.images  # newest 2 (incl. current) kept
    assert load(ids[0]).meta.get("image_pruned") is True
    assert report.log_rows == 1
    assert report.metric_rows == 1
    assert not report.errors


async def test_agent_detects_crash_and_collects_metrics(client: httpx.AsyncClient) -> None:
    _, project, deployment = await start(client)
    h = Harness()
    h.run(deployment["id"])
    agent = Agent(h.settings, h.docker, h.store, sync_session, get_sync_redis())  # type: ignore[arg-type]

    agent.collect_metrics()
    metrics = (
        await client.get(f"/api/projects/{project['id']}/metrics", headers=await _login(client))
    ).json()
    assert metrics["current"]["cpu_usage"] == 12.5

    container = load(deployment["id"]).container_id
    assert container
    h.docker.containers[container].update(running=False, exit_code=137)
    agent.check_crash(container)
    crashed = load(deployment["id"])
    assert crashed.status == DeploymentStatus.FAILED
    assert crashed.failure_reason == FailureReason.RUNTIME_CRASHED
    assert crashed.error_message == "Container exited with code 137"
    assert load_project(project["id"]).status == ProjectStatus.CRASHED


async def test_agent_ignores_platform_initiated_stops(client: httpx.AsyncClient) -> None:
    headers, project, deployment = await start(client)
    user_id = uuid.UUID((await client.get("/api/auth/me", headers=headers)).json()["id"])
    h = Harness()
    h.run(deployment["id"])
    container = load(deployment["id"]).container_id
    assert container
    h.lifecycle.stop(uuid.UUID(project["id"]), user_id)
    agent = Agent(h.settings, h.docker, h.store, sync_session, get_sync_redis())  # type: ignore[arg-type]
    agent.check_crash(container)
    assert load(deployment["id"]).status == DeploymentStatus.STOPPED


async def _login(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", json={"email": "alice@example.com", "password": "Sup3r-Secret-pass"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_undecryptable_environment_fails_fast(client: httpx.AsyncClient) -> None:
    headers, project, deployment = await start(client)
    await client.post(f"/api/projects/{project['id']}/env", json={"key": "A", "value": "b"}, headers=headers)
    with sync_session() as session:  # simulate a value encrypted with a key we no longer have
        session.execute(update(EnvironmentVariable).values(encrypted_value="gAAAAA-not-a-valid-token"))
    h = Harness()
    assert h.run(deployment["id"]) == DeploymentStatus.FAILED
    stored = load(deployment["id"])
    assert stored.failure_reason == FailureReason.INTERNAL_ERROR
    assert "ENCRYPTION_KEYS" in (stored.error_message or "")
    assert not h.docker.events_log
