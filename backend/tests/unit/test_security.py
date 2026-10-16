from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.fernet import Fernet

from app.core.config import Settings
from app.core.crypto import SecretBox, SecretBoxError
from app.core.errors import Unauthorized
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.routing.domains import SlugError, slugify, validate_slug, with_suffix

SETTINGS = Settings(jwt_secret="s" * 40)


def test_password_hashing() -> None:
    hashed = hash_password("Correct-Horse-1")
    assert hashed.startswith("$argon2id$")
    assert verify_password("Correct-Horse-1", hashed)
    assert not verify_password("wrong", hashed)
    assert not verify_password("anything", None)
    assert not verify_password("anything", "not-a-hash")


def test_access_token_round_trip() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(SETTINGS, user_id)
    assert decode_access_token(SETTINGS, token.token) == user_id


def _forge(payload: dict[str, object], secret: str = "s" * 40, alg: str = "HS256") -> str:
    return jwt.encode(payload, secret, algorithm=alg)


def _claims(**overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": str(uuid.uuid4()),
        "typ": "access",
        "iss": SETTINGS.jwt_issuer,
        "aud": SETTINGS.jwt_audience,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return claims


@pytest.mark.parametrize(
    "token",
    [
        _forge(_claims(), secret="other-secret" * 4),
        _forge(_claims(typ="refresh")),
        _forge(_claims(aud="someone-else")),
        _forge(_claims(iss="evil")),
        _forge(_claims(sub="not-a-uuid")),
        "a.b.c",
    ],
)
def test_invalid_tokens_rejected(token: str) -> None:
    with pytest.raises(Unauthorized):
        decode_access_token(SETTINGS, token)


def test_none_algorithm_rejected() -> None:
    header = "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"  # {"alg":"none","typ":"JWT"}
    body = jwt.utils.base64url_encode(json.dumps(_claims(), default=str).encode()).decode()
    with pytest.raises(Unauthorized):
        decode_access_token(SETTINGS, f"{header}.{body}.")


def test_expired_token() -> None:
    past = datetime.now(UTC) - timedelta(hours=1)
    token = _forge(_claims(iat=past, exp=past + timedelta(minutes=1)))
    with pytest.raises(Unauthorized) as exc:
        decode_access_token(SETTINGS, token)
    assert exc.value.code == "TOKEN_EXPIRED"


def test_token_hash_is_stable_and_not_reversible() -> None:
    assert hash_token("abc") == hash_token("abc")
    assert "abc" not in hash_token("abc")


def test_production_refuses_insecure_settings() -> None:
    with pytest.raises(ValueError, match="Insecure production configuration"):
        Settings(environment="production", jwt_secret="short")


def test_secret_box_round_trip_and_rotation() -> None:
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    legacy = SecretBox([old]).encrypt("s3cr3t")
    rotated_box = SecretBox([new, old])
    assert rotated_box.decrypt(legacy) == "s3cr3t"
    assert SecretBox([new]).decrypt(rotated_box.rotate(legacy)) == "s3cr3t"
    with pytest.raises(SecretBoxError):
        SecretBox([new]).decrypt(legacy)


def test_secret_box_requires_valid_keys() -> None:
    with pytest.raises(SecretBoxError):
        SecretBox([])
    with pytest.raises(SecretBoxError):
        SecretBox(["not-a-fernet-key"])


@pytest.mark.parametrize("slug", ["my-api", "abc", "a1-b2-c3", "x" * 40])
def test_valid_slugs(slug: str) -> None:
    assert validate_slug(slug) == slug


@pytest.mark.parametrize(
    "slug", ["ab", "x" * 41, "-abc", "abc-", "a--b", "ABC", "a_b", "api", "www", "my.app"]
)
def test_invalid_slugs(slug: str) -> None:
    with pytest.raises(SlugError):
        validate_slug(slug)


def test_slugify() -> None:
    assert slugify("My Cool API!") == "my-cool-api"
    fallback = slugify("ñ")  # nothing usable left: falls back to a random valid slug
    assert validate_slug(fallback) == fallback
    assert validate_slug(slugify("api"))  # reserved names get a suffix
    suffixed = with_suffix("a" * 40)
    assert len(suffixed) <= 40
    assert validate_slug(suffixed)


def test_settings_read_secret_files_and_ignore_empty_env(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    secrets_dir = Path(str(tmp_path))
    (secrets_dir / "jwt_secret").write_text("from-file-" + "x" * 40)
    (secrets_dir / "postgres_password").write_text("db-pass")
    monkeypatch.setenv("JWT_SECRET", "")  # what docker compose passes for an unset override
    monkeypatch.setenv("PUBLIC_PORT", "")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(_secrets_dir=str(secrets_dir), _env_file=None)
    assert settings.jwt_secret.get_secret_value().startswith("from-file-")
    assert settings.public_port is None
    assert ":db-pass@" in settings.database_url
