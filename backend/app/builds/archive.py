"""Defensive ZIP handling.

`inspect_archive` validates structure without extracting (used by the API on upload).
`extract_archive` re-validates and extracts while counting the bytes actually written, so a
forged header cannot smuggle a zip bomb past the declared sizes.

Rejected: absolute paths, `..` components, drive letters, symlinks, encrypted entries,
too many entries, too large in total, suspicious compression ratios.
"""

from __future__ import annotations

import os
import shutil
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

CHUNK = 64 * 1024
RATIO_CHECK_MIN_BYTES = 1024 * 1024  # small files can legitimately compress extremely well


class ArchiveError(ValueError):
    pass


@dataclass(frozen=True)
class ArchiveLimits:
    max_entries: int
    max_total_bytes: int
    max_ratio: int


@dataclass(frozen=True)
class ArchiveInfo:
    file_count: int
    total_bytes: int
    root_prefix: str | None  # single top-level folder (e.g. "repo-main/") that gets stripped


def _member_path(name: str) -> PurePosixPath | None:
    """Normalise a member name; return None for entries that should be skipped."""
    normalised = name.replace("\\", "/")
    if normalised.startswith("/") or (len(normalised) > 1 and normalised[1] == ":"):
        raise ArchiveError(f"Archive contains an absolute path: {name!r}")
    parts = [p for p in normalised.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ArchiveError(f"Archive contains a path traversal entry: {name!r}")
    if any("\x00" in p for p in parts):
        raise ArchiveError("Archive contains an invalid file name")
    if not parts or parts[0] == "__MACOSX":
        return None
    return PurePosixPath(*parts)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def inspect_archive(path: Path, limits: ArchiveLimits) -> ArchiveInfo:
    if not zipfile.is_zipfile(path):
        raise ArchiveError("File is not a valid ZIP archive")
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise ArchiveError("Corrupted ZIP archive") from exc

    if len(infos) > limits.max_entries:
        raise ArchiveError(f"Archive has too many entries (max {limits.max_entries})")

    total = 0
    files = 0
    top_levels: set[str] = set()
    has_root_file = False
    for info in infos:
        member = _member_path(info.filename)
        if member is None:
            continue
        if info.flag_bits & 0x1:
            raise ArchiveError("Encrypted archives are not supported")
        if _is_symlink(info):
            raise ArchiveError(f"Archive contains a symbolic link: {info.filename!r}")
        top_levels.add(member.parts[0])
        if info.is_dir():
            continue
        if len(member.parts) == 1:
            has_root_file = True
        files += 1
        total += info.file_size
        if total > limits.max_total_bytes:
            raise ArchiveError(f"Archive expands beyond {limits.max_total_bytes // (1024 * 1024)} MB")
        if (
            info.file_size > RATIO_CHECK_MIN_BYTES
            and info.compress_size > 0
            and info.file_size / info.compress_size > limits.max_ratio
        ):
            raise ArchiveError(f"Suspicious compression ratio for {info.filename!r}")

    if files == 0:
        raise ArchiveError("Archive is empty")
    root_prefix = None
    if len(top_levels) == 1 and not has_root_file:
        root_prefix = next(iter(top_levels))
    return ArchiveInfo(file_count=files, total_bytes=total, root_prefix=root_prefix)


def extract_archive(path: Path, destination: Path, limits: ArchiveLimits) -> ArchiveInfo:
    info = inspect_archive(path, limits)
    destination.mkdir(parents=True, exist_ok=True)
    dest_root = destination.resolve()
    written = 0

    with zipfile.ZipFile(path) as zf:
        for entry in zf.infolist():
            member = _member_path(entry.filename)
            if member is None:
                continue
            if info.root_prefix is not None:
                if len(member.parts) == 1:
                    continue  # the root folder itself
                member = PurePosixPath(*member.parts[1:])
            target = (dest_root / member).resolve()
            if not target.is_relative_to(dest_root):
                raise ArchiveError(f"Entry escapes the workspace: {entry.filename!r}")
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(entry) as src, open(target, "wb") as dst:
                while chunk := src.read(CHUNK):
                    written += len(chunk)
                    if written > limits.max_total_bytes:
                        raise ArchiveError("Archive expands beyond the allowed size")
                    dst.write(chunk)
            mode = (entry.external_attr >> 16) & 0o777
            os.chmod(target, 0o755 if mode & 0o111 else 0o644)
    return info


def assert_no_escaping_symlinks(root: Path) -> None:
    """Reject symlinks that resolve outside the workspace (defence for git checkouts)."""
    resolved_root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        for name in [*dirnames, *filenames]:
            candidate = Path(dirpath) / name
            if candidate.is_symlink():
                target = candidate.resolve()
                if not target.is_relative_to(resolved_root):
                    raise ArchiveError(f"Source contains a symlink escaping the workspace: {name}")


def directory_size(root: Path) -> int:
    total = 0
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            candidate = Path(dirpath) / name
            if not candidate.is_symlink():
                total += candidate.stat().st_size
    return total


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
