"""Small builders shared by unit and integration tests."""

from __future__ import annotations

import io
import stat
import zipfile
from collections.abc import Mapping


def make_zip(files: Mapping[str, bytes | str], *, symlinks: dict[str, str] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        for name, target in (symlinks or {}).items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, target)
    return buffer.getvalue()


STATIC_SITE = {"index.html": "<h1>hello</h1>", "style.css": "body{}"}
