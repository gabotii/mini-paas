"""Git repository sources with SSRF protection.

Only public `https://` repositories are accepted. The hostname is resolved and every address
must be globally routable (no loopback, private, link-local or metadata addresses). The clone
then pins the validated IP with `http.curloptResolve`, which closes the DNS-rebinding window
between validation and the actual connection.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from app.builds.archive import ArchiveError, assert_no_escaping_symlinks, directory_size, remove_tree

_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]+$")


class GitSourceError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedRepository:
    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def validate_repository_url(url: str, *, allow_private: bool = False) -> ValidatedRepository:
    url = url.strip()
    if any(ch.isspace() for ch in url):
        raise GitSourceError("Repository URL cannot contain whitespace")
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise GitSourceError("Only https:// repository URLs are supported")
    if parts.username or parts.password or "@" in parts.netloc:
        raise GitSourceError("Credentials in repository URLs are not supported (public repos only)")
    if parts.query or parts.fragment:
        raise GitSourceError("Repository URL cannot contain a query string or fragment")
    host = parts.hostname or ""
    if not host or not _HOST_RE.match(host):
        raise GitSourceError("Repository URL has an invalid host")
    try:
        port = parts.port or 443
    except ValueError as exc:
        raise GitSourceError("Repository URL has an invalid port") from exc
    if not parts.path or not _PATH_RE.match(parts.path) or ".." in parts.path:
        raise GitSourceError("Repository URL has an invalid path")

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise GitSourceError(f"Could not resolve repository host {host!r}") from exc
    addresses = tuple(sorted({str(info[4][0]) for info in infos}))
    if not addresses:
        raise GitSourceError(f"Could not resolve repository host {host!r}")
    if not allow_private and not all(_is_public(a) for a in addresses):
        raise GitSourceError("Repository host resolves to a private or reserved address")
    return ValidatedRepository(url=url, host=host, port=port, addresses=addresses)


@dataclass(frozen=True)
class CloneResult:
    commit_hash: str
    size_bytes: int


def clone_repository(
    repo: ValidatedRepository,
    branch: str | None,
    destination: Path,
    *,
    timeout: int,
    max_bytes: int,
) -> CloneResult:
    pinned = repo.addresses[0]
    pinned_fmt = f"[{pinned}]" if ":" in pinned else pinned
    cmd = [
        "git",
        "-c", "protocol.allow=never",
        "-c", "protocol.https.allow=always",
        "-c", "http.followRedirects=false",
        "-c", f"http.curloptResolve={repo.host}:{repo.port}:{pinned_fmt}",
        "-c", "core.symlinks=false",
        "-c", "core.hooksPath=/dev/null",
        "-c", "submodule.recurse=false",
        "-c", "credential.helper=",
        "clone", "--depth=1", "--single-branch", "--no-tags",
    ]  # fmt: skip
    if branch:
        cmd += ["--branch", branch]
    cmd += ["--", repo.url, str(destination)]

    with tempfile.TemporaryDirectory(prefix="git-home-") as home:
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": home,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "/bin/false",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_LFS_SKIP_SMUDGE": "1",
        }
        try:
            proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            remove_tree(destination)
            raise GitSourceError(f"git clone timed out after {timeout}s") from exc
        if proc.returncode != 0:
            remove_tree(destination)
            stderr = proc.stderr.strip().splitlines()
            raise GitSourceError("git clone failed: " + (stderr[-1] if stderr else "unknown error"))

        rev = subprocess.run(
            ["git", "-C", str(destination), "rev-parse", "HEAD"],
            env=env, capture_output=True, text=True, timeout=30, check=False,
        )  # fmt: skip
    commit = rev.stdout.strip() if rev.returncode == 0 else ""

    remove_tree(destination / ".git")  # never ship git metadata into images
    try:
        assert_no_escaping_symlinks(destination)
    except ArchiveError as exc:
        raise GitSourceError(str(exc)) from exc
    size = directory_size(destination)
    if size > max_bytes:
        raise GitSourceError(f"Repository is larger than {max_bytes // (1024 * 1024)} MB")
    return CloneResult(commit_hash=commit, size_bytes=size)
