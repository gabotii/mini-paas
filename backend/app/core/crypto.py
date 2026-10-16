"""Symmetric encryption for secrets at rest (environment variable values).

Uses Fernet (AES-128-CBC + HMAC-SHA256, authenticated) through MultiFernet so keys can be
rotated: the first key in ENCRYPTION_KEYS encrypts, every key can decrypt.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import Settings, get_settings


class SecretBoxError(Exception):
    pass


class SecretBox:
    def __init__(self, keys: list[str]) -> None:
        if not keys:
            raise SecretBoxError(
                "ENCRYPTION_KEYS is not configured. Generate one with: "
                'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
        try:
            self._fernet = MultiFernet([Fernet(key.encode()) for key in keys])
        except (ValueError, TypeError) as exc:
            raise SecretBoxError("ENCRYPTION_KEYS contains an invalid Fernet key") from exc

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise SecretBoxError("Unable to decrypt value (wrong key or corrupted data)") from exc

    def rotate(self, ciphertext: str) -> str:
        return self._fernet.rotate(ciphertext.encode()).decode()


def parse_keys(settings: Settings) -> list[str]:
    raw = settings.encryption_keys.get_secret_value()
    return [key.strip() for key in raw.split(",") if key.strip()]


@lru_cache
def get_secret_box() -> SecretBox:
    return SecretBox(parse_keys(get_settings()))
