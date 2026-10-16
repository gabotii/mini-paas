"""On-disk layout of the shared data volume.

<DATA_DIR>/sources/<project_id>/<upload_id>.zip   uploaded archives (kept for redeploys)
<DATA_DIR>/workspaces/<deployment_id>/            temporary build workspace (always removed)
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from app.builds.archive import remove_tree


class StoragePathError(ValueError):
    pass


class Storage:
    def __init__(self, data_dir: str | Path) -> None:
        self.root = Path(data_dir)

    @property
    def sources_root(self) -> Path:
        return self.root / "sources"

    @property
    def workspaces_root(self) -> Path:
        return self.root / "workspaces"

    def project_sources(self, project_id: uuid.UUID) -> Path:
        return self.sources_root / str(project_id)

    def new_archive_path(self, project_id: uuid.UUID) -> tuple[Path, str]:
        """Return (absolute path, path relative to DATA_DIR) for a new upload."""
        directory = self.project_sources(project_id)
        directory.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}.zip"
        return directory / name, f"sources/{project_id}/{name}"

    def resolve(self, relative: str) -> Path:
        """Resolve a DATA_DIR-relative path stored in the database, refusing escapes."""
        candidate = (self.root / relative).resolve()
        if not candidate.is_relative_to(self.root.resolve()):
            raise StoragePathError("Stored path escapes the data directory")
        return candidate

    def workspace(self, deployment_id: uuid.UUID) -> Path:
        return self.workspaces_root / str(deployment_id)

    def prune_archives(self, project_id: uuid.UUID, keep: set[str]) -> int:
        """Delete uploaded archives of a project except the relative paths in `keep`."""
        removed = 0
        directory = self.project_sources(project_id)
        if not directory.is_dir():
            return 0
        for archive in directory.glob("*.zip"):
            if f"sources/{project_id}/{archive.name}" not in keep:
                archive.unlink(missing_ok=True)
                removed += 1
        return removed

    def remove_project(self, project_id: uuid.UUID) -> None:
        remove_tree(self.project_sources(project_id))

    def prune_workspaces(self, max_age_seconds: int, active: set[str]) -> int:
        removed = 0
        if not self.workspaces_root.is_dir():
            return 0
        cutoff = time.time() - max_age_seconds
        for entry in self.workspaces_root.iterdir():
            if entry.name in active:
                continue
            if entry.stat().st_mtime < cutoff:
                remove_tree(entry)
                removed += 1
        return removed
