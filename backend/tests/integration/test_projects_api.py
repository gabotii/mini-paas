from __future__ import annotations

import socket
from typing import Any

import httpx
import pytest

from app.core.config import get_settings
from tests.conftest import RecordingQueue, create_project, register

pytestmark = pytest.mark.integration


async def test_create_and_list(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    project = await create_project(client, headers, name="My Cool API", description="demo")
    assert project["slug"] == "my-cool-api"
    assert project["url"] == "http://my-cool-api.localhost"
    assert project["status"] == "NEW"
    assert project["runtime"] == "auto"
    assert project["has_source"] is False
    assert project["cpu_limit"] == get_settings().app_cpu_default

    listing = await client.get("/api/projects", headers=headers)
    assert listing.status_code == 200
    assert [p["id"] for p in listing.json()["items"]] == [project["id"]]
    assert listing.json()["limits"]["max_upload_mb"] == get_settings().max_upload_mb


async def test_slug_rules(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    await create_project(client, headers, slug="taken-name")
    dup = await client.post(
        "/api/projects", json={"name": "x", "source_type": "zip", "slug": "taken-name"}, headers=headers
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "SLUG_TAKEN"
    normalised = await create_project(client, headers, slug="  Mixed-Case ")
    assert normalised["slug"] == "mixed-case"
    for bad in ("api", "a", "bad_slug", "-x-", "x--y", "dots.not.allowed"):
        response = await client.post(
            "/api/projects", json={"name": "x", "source_type": "zip", "slug": bad}, headers=headers
        )
        assert response.status_code == 422, bad
    # Derived slugs get a suffix instead of failing.
    second = await create_project(client, headers, name="Taken Name")
    assert second["slug"].startswith("taken-name-")


async def test_resource_limits_validated(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    response = await client.post(
        "/api/projects",
        json={"name": "big", "source_type": "zip", "cpu_limit": 8, "memory_limit_mb": 8192},
        headers=headers,
    )
    assert response.status_code == 422
    fields = {e["field"] for e in response.json()["error"]["details"]["errors"]}
    assert fields == {"cpu_limit", "memory_limit_mb"}


async def test_project_quota(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "max_projects_per_user", 2)
    headers = await register(client)
    await create_project(client, headers, name="one")
    await create_project(client, headers, name="two")
    third = await client.post("/api/projects", json={"name": "three", "source_type": "zip"}, headers=headers)
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "QUOTA_EXCEEDED"


async def test_git_project_validation(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    headers = await register(client)

    def resolve(host: str, port: int, *a: Any, **k: Any) -> list[Any]:
        address = "10.0.0.8" if host == "git.internal" else "140.82.112.3"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    missing = await client.post("/api/projects", json={"name": "g", "source_type": "git"}, headers=headers)
    assert missing.status_code == 422
    ssrf = await client.post(
        "/api/projects",
        json={"name": "g", "source_type": "git", "repository_url": "https://git.internal/a/b"},
        headers=headers,
    )
    assert ssrf.status_code == 422
    assert "private" in ssrf.json()["error"]["message"]
    ok = await create_project(
        client,
        headers,
        name="g",
        source_type="git",
        repository_url="https://github.com/acme/app.git",
        repository_branch="main",
    )
    assert ok["has_source"] is True
    assert ok["repository_branch"] == "main"


async def test_update_project(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    project = await create_project(client, headers)
    response = await client.patch(
        f"/api/projects/{project['id']}",
        json={"name": "Renamed", "port": 9000, "start_command": "python app.py", "health_check_path": "/health",
              "runtime": "python", "memory_limit_mb": 128},
        headers=headers,
    )  # fmt: skip
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["name"], body["port"], body["start_command"], body["health_check_path"]) == (
        "Renamed", 9000, "python app.py", "/health",
    )  # fmt: skip
    assert body["slug"] == project["slug"]  # slug is immutable
    bad = await client.patch(
        f"/api/projects/{project['id']}", json={"start_command": "a\nb"}, headers=headers
    )
    assert bad.status_code == 422
    bad_path = await client.patch(
        f"/api/projects/{project['id']}", json={"health_check_path": "no-slash"}, headers=headers
    )
    assert bad_path.status_code == 422


async def test_delete_project_is_queued_and_hidden(client: httpx.AsyncClient, queue: RecordingQueue) -> None:
    headers = await register(client)
    project = await create_project(client, headers)
    response = await client.delete(f"/api/projects/{project['id']}", headers=headers)
    assert response.status_code == 202
    assert queue.names() == ["delete"]
    assert (await client.get(f"/api/projects/{project['id']}", headers=headers)).status_code == 404
    assert (await client.get("/api/projects", headers=headers)).json()["items"] == []


async def test_delete_restores_project_when_queue_is_down(
    client: httpx.AsyncClient, queue: RecordingQueue
) -> None:
    headers = await register(client)
    project = await create_project(client, headers)
    queue.fail = True
    response = await client.delete(f"/api/projects/{project['id']}", headers=headers)
    assert response.status_code == 503
    assert (await client.get(f"/api/projects/{project['id']}", headers=headers)).status_code == 200


async def test_users_cannot_touch_each_others_projects(client: httpx.AsyncClient) -> None:
    alice = await register(client, "alice@example.com")
    bob = await register(client, "bob@example.com", "Bob")
    project = await create_project(client, alice)
    pid = project["id"]
    attempts = [
        ("GET", f"/api/projects/{pid}", None),
        ("PATCH", f"/api/projects/{pid}", {"name": "pwned"}),
        ("DELETE", f"/api/projects/{pid}", None),
        ("POST", f"/api/projects/{pid}/deploy", None),
        ("POST", f"/api/projects/{pid}/stop", None),
        ("POST", f"/api/projects/{pid}/restart", None),
        ("GET", f"/api/projects/{pid}/deployments", None),
        ("GET", f"/api/projects/{pid}/logs", None),
        ("GET", f"/api/projects/{pid}/metrics", None),
        ("GET", f"/api/projects/{pid}/env", None),
        ("POST", f"/api/projects/{pid}/env", {"key": "A", "value": "b"}),
    ]
    for method, path, body in attempts:
        response = await client.request(method, path, json=body, headers=bob)
        assert response.status_code == 404, (method, path, response.status_code)
        assert response.json()["error"]["code"] == "PROJECT_NOT_FOUND"
    assert (await client.get("/api/projects", headers=bob)).json()["items"] == []
    assert (await client.get(f"/api/projects/{pid}", headers=alice)).json()["name"] == "My App"
