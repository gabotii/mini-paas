from __future__ import annotations

import socket
from typing import Any

import pytest

from app.builds.git_source import GitSourceError, validate_repository_url


def fake_resolver(monkeypatch: pytest.MonkeyPatch, *addresses: str) -> None:
    def getaddrinfo(host: str, port: int, *args: Any, **kwargs: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


def test_accepts_public_https(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_resolver(monkeypatch, "140.82.112.3")
    repo = validate_repository_url("https://github.com/acme/app.git")
    assert repo.host == "github.com"
    assert repo.port == 443
    assert repo.addresses == ("140.82.112.3",)


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://github.com/acme/app", "https"),
        ("git@github.com:acme/app.git", "https"),
        ("file:///etc/passwd", "https"),
        ("ext::sh -c touch% /tmp/pwned", "whitespace"),
        ("https://user:token@github.com/acme/app", "Credentials"),
        ("https://github.com/acme/app?x=1", "query"),
        ("https://github.com", "path"),
        ("https://github.com/acme/../app", "path"),
        ("https://exa mple.com/a", "whitespace"),
    ],
)
def test_rejects_unsafe_urls(monkeypatch: pytest.MonkeyPatch, url: str, message: str) -> None:
    fake_resolver(monkeypatch, "140.82.112.3")
    with pytest.raises(GitSourceError, match=message):
        validate_repository_url(url)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.5",
        "172.17.0.1",
        "192.168.1.10",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "::1",
        "fd00::1",
    ],
)
def test_rejects_private_and_metadata_addresses(monkeypatch: pytest.MonkeyPatch, address: str) -> None:
    fake_resolver(monkeypatch, address)
    with pytest.raises(GitSourceError, match="private or reserved"):
        validate_repository_url("https://internal.example.com/acme/app")


def test_rejects_when_any_address_is_private(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_resolver(monkeypatch, "140.82.112.3", "10.0.0.1")
    with pytest.raises(GitSourceError):
        validate_repository_url("https://mixed.example.com/a/b")


def test_private_allowed_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_resolver(monkeypatch, "10.0.0.5")
    assert validate_repository_url("https://git.internal/a/b", allow_private=True).addresses == ("10.0.0.5",)


def test_unresolvable_host(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_: Any, **__: Any) -> Any:
        raise socket.gaierror("nope")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(GitSourceError, match="resolve"):
        validate_repository_url("https://does-not-exist.invalid/a/b")
