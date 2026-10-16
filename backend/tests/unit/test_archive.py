from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.builds.archive import ArchiveError, ArchiveLimits, extract_archive, inspect_archive
from tests.helpers import STATIC_SITE, make_zip

LIMITS = ArchiveLimits(max_entries=100, max_total_bytes=5 * 1024 * 1024, max_ratio=100)


def write(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "upload.zip"
    path.write_bytes(data)
    return path


def test_extracts_files(tmp_path: Path) -> None:
    archive = write(tmp_path, make_zip({**STATIC_SITE, "assets/app.js": "console.log(1)"}))
    info = extract_archive(archive, tmp_path / "ws", LIMITS)
    assert info.file_count == 3
    assert (tmp_path / "ws" / "index.html").read_text() == "<h1>hello</h1>"
    assert (tmp_path / "ws" / "assets" / "app.js").is_file()


def test_strips_single_top_level_folder(tmp_path: Path) -> None:
    archive = write(tmp_path, make_zip({"repo-main/index.html": "x", "repo-main/src/a.txt": "y"}))
    info = extract_archive(archive, tmp_path / "ws", LIMITS)
    assert info.root_prefix == "repo-main"
    assert (tmp_path / "ws" / "index.html").is_file()
    assert (tmp_path / "ws" / "src" / "a.txt").is_file()


def test_ignores_macosx_metadata(tmp_path: Path) -> None:
    archive = write(tmp_path, make_zip({"index.html": "x", "__MACOSX/._index.html": "junk"}))
    extract_archive(archive, tmp_path / "ws", LIMITS)
    assert not (tmp_path / "ws" / "__MACOSX").exists()


@pytest.mark.parametrize(
    "name", ["../evil.txt", "a/../../evil.txt", "/etc/passwd", "C:/windows/x", "..\\evil.txt"]
)
def test_rejects_path_traversal(tmp_path: Path, name: str) -> None:
    archive = write(tmp_path, make_zip({"index.html": "x", name: "pwned"}))
    with pytest.raises(ArchiveError):
        inspect_archive(archive, LIMITS)
    assert not (tmp_path.parent / "evil.txt").exists()


def test_rejects_symlinks(tmp_path: Path) -> None:
    archive = write(tmp_path, make_zip({"index.html": "x"}, symlinks={"link": "/etc/passwd"}))
    with pytest.raises(ArchiveError, match="symbolic link"):
        inspect_archive(archive, LIMITS)


def test_rejects_too_many_entries(tmp_path: Path) -> None:
    archive = write(tmp_path, make_zip({f"f{i}.txt": "x" for i in range(101)}))
    with pytest.raises(ArchiveError, match="too many entries"):
        inspect_archive(archive, LIMITS)


def test_rejects_zip_bomb_ratio(tmp_path: Path) -> None:
    archive = write(tmp_path, make_zip({"index.html": "x", "bomb.bin": b"\0" * (4 * 1024 * 1024)}))
    with pytest.raises(ArchiveError, match="compression ratio"):
        inspect_archive(archive, LIMITS)


def test_rejects_total_size(tmp_path: Path) -> None:
    import os

    files = {f"r{i}.bin": os.urandom(1024 * 1024) for i in range(6)}
    archive = write(tmp_path, make_zip(files))
    with pytest.raises(ArchiveError, match="expands beyond"):
        inspect_archive(archive, LIMITS)


def test_extraction_counts_real_bytes_not_headers(tmp_path: Path) -> None:
    """A forged header (declared size smaller than the real data) must not bypass the limit."""
    import os

    path = tmp_path / "forged.zip"
    path.write_bytes(make_zip({"big.bin": os.urandom(2 * 1024 * 1024)}))
    small = ArchiveLimits(max_entries=10, max_total_bytes=1024 * 1024, max_ratio=100)
    with zipfile.ZipFile(path) as zf:
        assert zf.infolist()[0].file_size > small.max_total_bytes
    with pytest.raises(ArchiveError):
        extract_archive(path, tmp_path / "ws", small)


def test_rejects_non_zip_and_empty(tmp_path: Path) -> None:
    with pytest.raises(ArchiveError, match="not a valid ZIP"):
        inspect_archive(write(tmp_path, b"not a zip at all"), LIMITS)
    with pytest.raises(ArchiveError, match="empty"):
        inspect_archive(write(tmp_path, make_zip({})), LIMITS)


def test_rejects_encrypted_entries(tmp_path: Path) -> None:
    data = bytearray(make_zip({"index.html": "x"}))
    # Set the "encrypted" general purpose flag in the local and central headers.
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        pos = data.find(signature)
        data[pos + offset] |= 0x1
    with pytest.raises(ArchiveError, match="Encrypted"):
        inspect_archive(write(tmp_path, bytes(data)), LIMITS)
