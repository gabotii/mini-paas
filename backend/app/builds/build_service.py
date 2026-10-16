"""BuildService — turns a deployment's source into a Docker image.

Source → Validation → Workspace → Runtime detection → Dockerfile generation → Build → Image
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.builds.archive import ArchiveError, ArchiveLimits, extract_archive
from app.builds.dockerfiles import BuildPlan, BuildPlanError, prepare_build
from app.builds.git_source import GitSourceError, clone_repository, validate_repository_url
from app.builds.runtime_detection import RuntimeDetectionError, resolve_runtime
from app.builds.workspace import Storage, StoragePathError
from app.core.config import Settings
from app.docker.service import BuildOptions, DockerService
from app.models import Runtime, SourceType


class SourceError(Exception):
    """The user's source could not be prepared (bad archive, clone failure, unknown runtime)."""


@dataclass(frozen=True)
class SourceSpec:
    source_type: SourceType
    archive: str | None
    repository_url: str | None
    branch: str | None


@dataclass(frozen=True)
class BuildOutput:
    image_name: str
    image_id: str
    runtime: Runtime
    plan: BuildPlan
    commit_hash: str | None
    detection_reason: str


def image_name(prefix: str, project_id: uuid.UUID, number: int) -> str:
    return f"{prefix}/project-{project_id}:deployment-{number}"


class BuildService:
    def __init__(self, settings: Settings, docker: DockerService, storage: Storage) -> None:
        self.settings = settings
        self.docker = docker
        self.storage = storage

    def build_options(self) -> BuildOptions:
        return BuildOptions(
            timeout_seconds=self.settings.build_timeout_seconds,
            builder_cpu=self.settings.build_cpu_limit,
            builder_memory_mb=self.settings.build_memory_mb,
        )

    def prepare_source(self, source: SourceSpec, workspace: Path, log: Callable[[str], None]) -> str | None:
        """Materialise the source in `workspace`. Returns the commit hash for git sources."""
        limits = ArchiveLimits(
            max_entries=self.settings.max_archive_entries,
            max_total_bytes=self.settings.max_extracted_bytes,
            max_ratio=self.settings.max_compression_ratio,
        )
        if source.source_type == SourceType.ZIP:
            if not source.archive:
                raise SourceError("No source archive uploaded for this project")
            try:
                archive = self.storage.resolve(source.archive)
            except StoragePathError as exc:
                raise SourceError(str(exc)) from exc
            if not archive.is_file():
                raise SourceError("The uploaded archive is no longer available; upload it again")
            log("Extracting uploaded archive...")
            try:
                info = extract_archive(archive, workspace, limits)
            except ArchiveError as exc:
                raise SourceError(str(exc)) from exc
            log(f"Extracted {info.file_count} files ({info.total_bytes / 1024:.0f} KB)")
            return None

        if not source.repository_url:
            raise SourceError("Project has no repository URL")
        try:
            repo = validate_repository_url(
                source.repository_url, allow_private=self.settings.allow_private_git_hosts
            )
            log(f"Cloning {repo.url}" + (f" (branch {source.branch})" if source.branch else "") + "...")
            result = clone_repository(
                repo,
                source.branch,
                workspace,
                timeout=self.settings.git_clone_timeout_seconds,
                max_bytes=self.settings.max_extracted_bytes,
            )
        except GitSourceError as exc:
            raise SourceError(str(exc)) from exc
        log(f"Checked out commit {result.commit_hash[:12] or 'unknown'}")
        return result.commit_hash or None

    def plan(
        self, workspace: Path, requested: Runtime, start_command: str | None, log: Callable[[str], None]
    ) -> tuple[BuildPlan, str]:
        log("Detecting runtime...")
        try:
            detection = resolve_runtime(requested, workspace)
            log(f"Runtime: {detection.runtime} ({detection.reason})")
            plan = prepare_build(workspace, detection.runtime, start_command)
        except (RuntimeDetectionError, BuildPlanError) as exc:
            raise SourceError(str(exc)) from exc
        for note in plan.notes:
            log(note)
        return plan, detection.reason

    def build_project(
        self,
        *,
        project_id: uuid.UUID,
        deployment_id: uuid.UUID,
        number: int,
        source_path: Path,
        plan: BuildPlan,
        on_line: Callable[[str], None],
        should_cancel: Callable[[], bool],
    ) -> tuple[str, str]:
        tag = image_name(self.settings.image_prefix, project_id, number)
        image_id = self.docker.build_image(
            context_dir=str(source_path),
            dockerfile=plan.dockerfile,
            tag=tag,
            labels={
                "paas.managed": "true",
                "paas.project_id": str(project_id),
                "paas.deployment_id": str(deployment_id),
            },
            options=self.build_options(),
            on_line=on_line,
            should_cancel=should_cancel,
        )
        return tag, image_id
