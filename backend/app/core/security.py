"""Password hashing (Argon2id), JWT access tokens and opaque refresh tokens."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings
from app.core.errors import Unauthorized

_hasher = PasswordHasher()  # Argon2id with OWASP-recommended defaults
# Used to equalise timing when the user does not exist (prevents user enumeration).
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@dataclass(frozen=True)
class AccessToken:
    token: str
    expires_in: int


def create_access_token(settings: Settings, user_id: uuid.UUID) -> AccessToken:
    now = datetime.now(UTC)
    ttl = settings.access_token_ttl_seconds
    payload = {
        "sub": str(user_id),
        "typ": "access",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(seconds=ttl),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=JWT_ALGORITHM)
    return AccessToken(token=token, expires_in=ttl)


def decode_access_token(settings: Settings, token: str) -> uuid.UUID:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[JWT_ALGORITHM],  # pinned: rejects "none" and algorithm confusion
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Access token expired", code="TOKEN_EXPIRED") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("Invalid access token", code="INVALID_TOKEN") from exc
    if payload.get("typ") != "access":
        raise Unauthorized("Invalid access token", code="INVALID_TOKEN")
    try:
        return uuid.UUID(payload["sub"])
    except (ValueError, TypeError) as exc:
        raise Unauthorized("Invalid access token", code="INVALID_TOKEN") from exc


def generate_opaque_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """Refresh tokens and WebSocket tickets are stored hashed; a DB leak does not leak sessions."""
    return hashlib.sha256(token.encode()).hexdigest()
