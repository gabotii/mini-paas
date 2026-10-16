"""Public hostnames for applications.

The MVP serves every app at `<slug>.<APPS_BASE_DOMAIN>` (`<slug>.localhost` locally, which
browsers resolve to 127.0.0.1). Custom domains will plug in here: `hosts_for()` is the single
place that decides which hostnames route to a project.
"""

from __future__ import annotations

import re
import secrets

from app.core.config import Settings

SLUG_MIN = 3
SLUG_MAX = 40
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){2,39}$")

RESERVED_SLUGS = frozenset(
    {
        "admin", "api", "app", "apps", "assets", "auth", "billing", "blog", "cdn", "console",
        "dashboard", "dev", "docs", "ftp", "help", "internal", "localhost", "mail", "minipaas",
        "mini-paas", "paas", "platform", "proxy", "root", "smtp", "static", "status", "support",
        "system", "test", "traefik", "www",
    }
)  # fmt: skip


class SlugError(ValueError):
    pass


def validate_slug(slug: str) -> str:
    if not SLUG_MIN <= len(slug) <= SLUG_MAX:
        raise SlugError(f"Slug must be between {SLUG_MIN} and {SLUG_MAX} characters")
    if not SLUG_RE.match(slug):
        raise SlugError(
            "Slug may contain lowercase letters, digits and single hyphens, "
            "and must start and end with a letter or digit"
        )
    if slug in RESERVED_SLUGS:
        raise SlugError(f"'{slug}' is a reserved name")
    return slug


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:SLUG_MAX].strip("-")
    if len(slug) < SLUG_MIN:
        slug = f"app-{slug}" if slug else "app"
        slug = slug[:SLUG_MAX]
    if len(slug) < SLUG_MIN or slug in RESERVED_SLUGS:
        slug = f"{slug}-{secrets.token_hex(2)}"
    return slug


def with_suffix(slug: str) -> str:
    suffix = secrets.token_hex(3)
    return f"{slug[: SLUG_MAX - len(suffix) - 1].rstrip('-')}-{suffix}"


class DomainService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def default_host(self, slug: str) -> str:
        return f"{slug}.{self._settings.apps_base_domain}"

    def hosts_for(self, slug: str) -> list[str]:
        return [self.default_host(slug)]

    def app_url(self, slug: str) -> str:
        port = f":{self._settings.public_port}" if self._settings.public_port else ""
        return f"{self._settings.public_scheme}://{self.default_host(slug)}{port}"
