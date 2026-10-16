"""Generate per-installation secrets once: `python -m app.cli.init_secrets /run/paas-secrets`.

Existing files are never overwritten (rotating ENCRYPTION_KEYS would make stored env vars
unreadable). Files are mode 0440, owned by root (postgres for its password) with the API's
group (10001), so only platform containers that mount the volume can read them.
"""

from __future__ import annotations

import os
import secrets
import sys
from collections.abc import Callable
from pathlib import Path

from cryptography.fernet import Fernet

GENERATORS: dict[str, Callable[[], str]] = {
    "jwt_secret": lambda: secrets.token_urlsafe(48),
    "encryption_keys": lambda: Fernet.generate_key().decode(),
    "internal_api_token": lambda: secrets.token_urlsafe(32),
    "postgres_password": lambda: secrets.token_urlsafe(24),
}

APP_UID = 10001
# Postgres runs as uid 70 in the alpine image and reads its password file as owner.
POSTGRES_UID = 70


def main(argv: list[str]) -> int:
    target = Path(argv[1] if len(argv) > 1 else "/run/paas-secrets")
    target.mkdir(parents=True, exist_ok=True)
    for name, generate in GENERATORS.items():
        path = target / name
        if path.exists():
            print(f"{name}: kept existing")
            continue
        path.write_text(generate(), encoding="utf-8")
        print(f"{name}: generated")
    if hasattr(os, "chown"):  # POSIX only (inside the container)
        for path in target.iterdir():
            # Owner root (proxy/worker run as root without CAP_DAC_OVERRIDE), group = API user.
            owner = POSTGRES_UID if path.name == "postgres_password" else 0
            os.chown(path, owner, APP_UID)
            os.chmod(path, 0o440)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
