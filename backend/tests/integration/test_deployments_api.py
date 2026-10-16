from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update

from app.core.config import get_settings
from app.db.session import sync_session
from app.models import Deployment, DeploymentStatus, Project, ProjectStatus
from tests.conftest import RecordingQueue, create_project, register
from tests.helpers import STATIC_SITE, make_zip

pytestmark = pytest.mark.integration

ZIP = {"Content-Type": "application/zip"}


async def upload(
    client: httpx.AsyncClient, headers: dict[str, str], project_id: str, data: bytes | None = None
) -> httpx.Response:
    return await client.post(
        f"/api/projects/{project_id}/upload",
        content=data or make_zip(STATIC_SITE),
        headers={**headers, **ZIP},
    )


def finish(deployment_id: str, status: DeploymentStatus = DeploymentStatus.RUNNING, **fields: Any) -> None:
    """Simulate the worker finishing a deployment (the pipeline itself is tested separately)."""
    with sync_session() as session:
        deployment = session.get(Deployment, uuid.UUID(deployment_id))
        assert deployment is not None
        now = datetime.now(UTC)
        deployment.status = status
        deployment.started_at = deployment.started_at or now
        deployment.finished_at = now
        if status in (DeploymentStatus.RUNNING, DeploymentStatus.STOPPED):
            deployment.deployed_at = now
            deployment.image_name = fields.pop(
                "image_name", f"mini-paas/project-x:deployment-{deployment.number}"
            )
            deployment.container_id = "container-" + deployment_id[:8]
            deployment.internal_port = 8080
        for key, value in fields.items():
            setattr(deployment, key, value)
        project = session.get(Project, deployment.project_id)
        assert project is not None
        if status == DeploymentStatus.RUNNING:
            project.current_deployment_id = deployment.id
            project.status = ProjectStatus.RUNNING


async def test_upload_queues_deployment(client: httpx.AsyncClient, queue: RecordingQueue) -> None:
    headers = await register(client)
    project = await create_project(client, headers)
    response = await upload(client, headers, project["id"])
    assert response.status_code == 202, response.text
    deployment = response.json()
    assert deployment["status"] == "QUEUED"
    assert deployment["number"] == 1
    assert deployment["trigger"] == "upload"
    assert queue.calls == [("deploy", (uuid.UUID(deployment["id"]),))]

    detail = (await client.get(f"/api/projects/{project['id']}", headers=headers)).json()
    assert detail["has_source"] is True
    assert detail["active_deployment"]["id"] == deployment["id"]


async def test_one_active_deployment_per_project(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    project = await create_project(client, headers)
    assert (await upload(client, headers, project["id"])).status_code == 202
    again = await client.post(f"/api/projects/{project['id']}/deploy", headers=headers)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "DEPLOYMENT_IN_PROGRESS"


async def test_concurrent_deploys_only_one_wins(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "max_active_deployments_per_user", 50)
    headers = await register(client)
    project = await create_project(client, headers)
    assert (await upload(client, headers, project["id"])).status_code == 202
    finish(
        (await client.get(f"/api/projects/{project['id']}/deployments", headers=headers)).json()["items"][0][
            "id"
        ]
    )
    responses = await asyncio.gather(
        *[client.post(f"/api/projects/{project['id']}/deploy", headers=headers) for _ in range(8)]
    )
    codes = sorted(r.status_code for r in responses)
    assert codes.count(202) == 1
    assert codes.count(409) == 7
    with sync_session() as session:
        active = session.scalars(
            select(Deployment).where(Deployment.status.in_(DeploymentStatus.active()))
        ).all()
    assert len(active) == 1


async def test_per_user_concurrency_cap(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "max_active_deployments_per_user", 1)
    headers = await register(client)
    first = await create_project(client, headers, name="first")
    second = await create_project(client, headers, name="second")
    assert (await upload(client, headers, first["id"])).status_code == 202
    blocked = await upload(client, headers, second["id"])
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "TOO_MANY_DEPLOYMENTS"


async def test_deploy_requires_source(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    project = await create_project(client, headers)
    response = await client.post(f"/api/projects/{project['id']}/deploy", headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NO_SOURCE"


async def test_upload_validation(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    headers = await register(client)
    project = await create_project(client, headers)
    pid = project["id"]

    wrong_type = await client.post(
        f"/api/projects/{pid}/upload", content=b"x", headers={**headers, "Content-Type": "text/plain"}
    )
    assert wrong_type.status_code == 415

    not_zip = await upload(client, headers, pid, b"definitely not a zip")
    assert not_zip.status_code == 422
    assert not_zip.json()["error"]["code"] == "INVALID_ARCHIVE"

    traversal = await upload(client, headers, pid, make_zip({"index.html": "x", "../../etc/cron.d/x": "pwn"}))
    assert traversal.status_code == 422

    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    import os

    too_big = await upload(client, headers, pid, make_zip({"blob.bin": os.urandom(2 * 1024 * 1024)}))
    assert too_big.status_code == 413
    assert too_big.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"

    with sync_session() as session:
        assert session.scalar(select(Deployment)) is None  # nothing was queued for rejected uploads


async def test_failed_enqueue_marks_deployment_failed(
    client: httpx.AsyncClient, queue: RecordingQueue
) -> None:
    headers = await register(client)
    project = await create_project(client, headers)
    queue.fail = True
    response = await upload(client, headers, project["id"])
    assert response.status_code == 503
    with sync_session() as session:
        deployment = session.scalar(select(Deployment))
    assert deployment is not None
    assert deployment.status == DeploymentStatus.FAILED
    assert deployment.failure_reason == "INTERNAL_ERROR"


async def test_cancel_queued_deployment(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    project = await create_project(client, headers)
    deployment = (await upload(client, headers, project["id"])).json()
    response = await client.post(f"/api/deployments/{deployment['id']}/cancel", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    again = await client.post(f"/api/deployments/{deployment['id']}/cancel", headers=headers)
    assert again.status_code == 409


async def test_cancel_in_progress_sets_flag(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    project = await create_project(client, headers)
    deployment = (await upload(client, headers, project["id"])).json()
    with sync_session() as session:
        session.execute(update(Deployment).values(status=DeploymentStatus.BUILDING))
    response = await client.post(f"/api/deployments/{deployment['id']}/cancel", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "BUILDING"
    with sync_session() as session:
        assert session.scalar(select(Deployment.cancel_requested)) is True


async def test_history_and_detail(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    project = await create_project(client, headers)
    first = (await upload(client, headers, project["id"])).json()
    finish(first["id"])
    second = (await client.post(f"/api/projects/{project['id']}/deploy", headers=headers)).json()
    finish(second["id"], DeploymentStatus.FAILED, failure_reason="BUILD_FAILED", error_message="pip failed")

    page = (await client.get(f"/api/projects/{project['id']}/deployments", headers=headers)).json()
    assert page["total"] == 2
    assert [d["number"] for d in page["items"]] == [2, 1]
    assert page["items"][1]["succeeded"] is True
    assert page["items"][0]["failure_reason"] == "BUILD_FAILED"
    assert page["items"][0]["duration_seconds"] is not None

    detail = (await client.get(f"/api/deployments/{second['id']}", headers=headers)).json()
    assert detail["error_message"] == "pip failed"
    assert detail["can_rollback"] is False


async def test_rollback(client: httpx.AsyncClient, queue: RecordingQueue) -> None:
    headers = await register(client)
    project = await create_project(client, headers)
    first = (await upload(client, headers, project["id"])).json()
    finish(first["id"])
    second = (await client.post(f"/api/projects/{project['id']}/deploy", headers=headers)).json()
    finish(second["id"], DeploymentStatus.FAILED)

    running = await client.post(f"/api/deployments/{first['id']}/rollback", headers=headers)
    assert running.status_code == 409  # #1 is the one currently running
    assert running.json()["error"]["code"] == "ROLLBACK_NOT_POSSIBLE"
    failed = await client.post(f"/api/deployments/{second['id']}/rollback", headers=headers)
    assert failed.status_code == 409

    third = (await client.post(f"/api/projects/{project['id']}/deploy", headers=headers)).json()
    finish(third["id"])
    with sync_session() as session:  # #1 was superseded by #3
        session.execute(
            update(Deployment).where(Deployment.id == uuid.UUID(first["id"])).values(status="STOPPED")
        )

    rollback = await client.post(f"/api/deployments/{first['id']}/rollback", headers=headers)
    assert rollback.status_code == 202, rollback.text
    body = rollback.json()
    assert body["trigger"] == "rollback"
    assert body["rollback_of_id"] == first["id"]
    assert body["image_name"] == "mini-paas/project-x:deployment-1"  # no rebuild
    assert queue.names()[-1] == "deploy"


async def test_rollback_refused_when_image_pruned(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    project = await create_project(client, headers)
    first = (await upload(client, headers, project["id"])).json()
    finish(first["id"], DeploymentStatus.STOPPED, meta={"image_pruned": True})
    response = await client.post(f"/api/deployments/{first['id']}/rollback", headers=headers)
    assert response.status_code == 409


async def test_stop_restart_start_rules(client: httpx.AsyncClient, queue: RecordingQueue) -> None:
    headers = await register(client)
    project = await create_project(client, headers)
    pid = project["id"]
    assert (await client.post(f"/api/projects/{pid}/stop", headers=headers)).status_code == 409
    assert (await client.post(f"/api/projects/{pid}/restart", headers=headers)).status_code == 409
    start = await client.post(f"/api/projects/{pid}/start", headers=headers)
    assert start.json()["error"]["code"] == "NOTHING_TO_START"

    deployment = (await upload(client, headers, pid)).json()
    finish(deployment["id"])
    assert (await client.post(f"/api/projects/{pid}/restart", headers=headers)).status_code == 202
    assert (await client.post(f"/api/projects/{pid}/stop", headers=headers)).status_code == 202
    assert queue.names()[-2:] == ["restart", "stop"]
    assert (await client.post(f"/api/projects/{pid}/start", headers=headers)).json()["error"][
        "code"
    ] == "ALREADY_RUNNING"

    with sync_session() as session:  # the worker stops it
        session.execute(update(Project).values(status=ProjectStatus.STOPPED))
        session.execute(update(Deployment).values(status=DeploymentStatus.STOPPED))
    started = await client.post(f"/api/projects/{pid}/start", headers=headers)
    assert started.status_code == 202
    assert started.json()["trigger"] == "rollback"
    assert started.json()["rollback_of_id"] == deployment["id"]


async def test_deployments_of_other_users_are_invisible(client: httpx.AsyncClient) -> None:
    alice = await register(client, "alice@example.com")
    bob = await register(client, "bob@example.com", "Bob")
    project = await create_project(client, alice)
    deployment = (await upload(client, alice, project["id"])).json()
    for method, path in [
        ("GET", f"/api/deployments/{deployment['id']}"),
        ("POST", f"/api/deployments/{deployment['id']}/cancel"),
        ("POST", f"/api/deployments/{deployment['id']}/rollback"),
        ("GET", f"/api/deployments/{deployment['id']}/logs"),
    ]:
        response = await client.request(method, path, headers=bob)
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] == "DEPLOYMENT_NOT_FOUND"
    # And Bob cannot read Alice's logs through his own project either.
    own = await create_project(client, bob, name="bobs")
    sneaky = await client.get(
        f"/api/projects/{own['id']}/logs", params={"deployment_id": deployment["id"]}, headers=bob
    )
    assert sneaky.status_code == 404
