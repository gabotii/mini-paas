from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import sync_session
from app.models import AuditLog, User
from tests.conftest import PASSWORD, register

pytestmark = pytest.mark.integration


async def test_register_login_me(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/auth/register", json={"email": " Alice@Example.COM ", "password": PASSWORD, "name": "Alice"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "alice@example.com"
    assert body["token_type"] == "bearer"
    cookie = response.headers["set-cookie"]
    assert "paas_refresh=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie or "samesite=strict" in cookie.lower()
    assert "Path=/api/auth" in cookie

    login = await client.post("/api/auth/login", json={"email": "ALICE@example.com", "password": PASSWORD})
    assert login.status_code == 200
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"})
    assert me.status_code == 200
    assert me.json()["name"] == "Alice"


async def test_password_is_hashed_and_actions_audited(client: httpx.AsyncClient) -> None:
    await register(client)
    with sync_session() as session:
        user = session.scalar(select(User))
        assert user is not None
        assert PASSWORD not in user.password_hash
        assert user.password_hash.startswith("$argon2id$")
        actions = set(session.scalars(select(AuditLog.action)))
    assert "USER_REGISTERED" in actions


async def test_duplicate_email(client: httpx.AsyncClient) -> None:
    await register(client)
    response = await client.post(
        "/api/auth/register", json={"email": "alice@example.com", "password": PASSWORD, "name": "Again"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EMAIL_TAKEN"


@pytest.mark.parametrize("password", ["short1A!", "alllowercaseletters", "ALLUPPERCASE123"])
async def test_weak_passwords_rejected(client: httpx.AsyncClient, password: str) -> None:
    response = await client.post(
        "/api/auth/register", json={"email": "b@example.com", "password": password, "name": "B"}
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["errors"][0]["field"] == "password"


async def test_bad_credentials_do_not_reveal_which_part_failed(client: httpx.AsyncClient) -> None:
    await register(client)
    wrong_password = await client.post(
        "/api/auth/login", json={"email": "alice@example.com", "password": "nope"}
    )
    unknown_user = await client.post(
        "/api/auth/login", json={"email": "ghost@example.com", "password": "nope"}
    )
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()
    with sync_session() as session:
        failures = list(session.scalars(select(AuditLog).where(AuditLog.action == "USER_LOGIN_FAILED")))
    assert len(failures) == 2


async def test_protected_endpoints_require_token(client: httpx.AsyncClient) -> None:
    for method, path in [("GET", "/api/auth/me"), ("GET", "/api/projects"), ("POST", "/api/projects")]:
        response = await client.request(method, path)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"
    bad = await client.get("/api/projects", headers={"Authorization": "Bearer not.a.jwt"})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "INVALID_TOKEN"


async def test_refresh_rotates_and_detects_reuse(client: httpx.AsyncClient) -> None:
    await register(client)
    original = client.cookies.get("paas_refresh")
    assert original

    first = await client.post("/api/auth/refresh")
    assert first.status_code == 200
    rotated = client.cookies.get("paas_refresh")
    assert rotated and rotated != original

    # Concurrent tabs: replaying the just-rotated token within the grace window still works.
    client.cookies.set("paas_refresh", original, path="/api/auth")
    grace = await client.post("/api/auth/refresh")
    assert grace.status_code == 200

    # After logout the whole family is dead, including tokens still inside the grace window.
    await client.post("/api/auth/logout")
    client.cookies.clear()
    for token in (original, rotated):
        client.cookies.clear()
        client.cookies.set("paas_refresh", token, path="/api/auth")
        dead = await client.post("/api/auth/refresh")
        assert dead.status_code == 401


async def test_refresh_reuse_after_grace_revokes_family(client: httpx.AsyncClient) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from app.models import RefreshToken

    await register(client)
    original = client.cookies.get("paas_refresh")
    assert (await client.post("/api/auth/refresh")).status_code == 200
    latest = client.cookies.get("paas_refresh")
    with sync_session() as session:  # pretend the rotation happened long ago
        session.execute(
            update(RefreshToken)
            .where(RefreshToken.revoked_at.is_not(None))
            .values(revoked_at=datetime.now(UTC) - timedelta(minutes=5))
        )
    assert original
    assert latest
    client.cookies.clear()
    client.cookies.set("paas_refresh", original, path="/api/auth")
    stolen = await client.post("/api/auth/refresh")
    assert stolen.status_code == 401
    client.cookies.clear()
    client.cookies.set("paas_refresh", latest, path="/api/auth")
    legit = await client.post("/api/auth/refresh")
    assert legit.status_code == 401  # family revoked: attacker and victim must log in again
    with sync_session() as session:
        assert session.scalar(select(AuditLog).where(AuditLog.action == "REFRESH_TOKEN_REUSE")) is not None


async def test_refresh_without_cookie(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/auth/refresh")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"


async def test_login_rate_limit(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_login", "3/60")
    statuses = []
    for _ in range(5):
        response = await client.post("/api/auth/login", json={"email": "x@example.com", "password": "nope"})
        statuses.append(response.status_code)
    assert statuses[:3] == [401, 401, 401]
    assert statuses[3] == 429
    limited = await client.post("/api/auth/login", json={"email": "x@example.com", "password": "nope"})
    assert limited.json()["error"]["code"] == "RATE_LIMITED"
    assert int(limited.headers["retry-after"]) > 0


async def test_ws_ticket(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    response = await client.post("/api/auth/ws-ticket", headers=headers)
    assert response.status_code == 200
    assert response.json()["expires_in"] == 30


async def test_ws_ticket_rate_limit(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_ws_ticket", "2/60")
    headers = await register(client)
    codes = [(await client.post("/api/auth/ws-ticket", headers=headers)).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
