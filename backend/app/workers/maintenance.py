"""Periodic maintenance: stale-deployment reaper and resource cleanup.

Cleanup never removes an image that is needed for rollback: per project it keeps the image of
the serving deployment plus the images of the last IMAGE_RETENTION_PER_PROJECT successful
deployments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select

from app.builds.workspace import Storage
from app.core.config import Settings
from app.core.logging import get_logger
from app.docker.service import DockerError, DockerService
from app.models import (
    Deployment,
    DeploymentLog,
    DeploymentStatus,
    FailureReason,
    Project,
    ProjectMetric,
    RefreshToken,
)
from app.workers.store import DeploymentStore, SessionFactory

log = get_logger(__name__)


@dataclass
class CleanupReport:
    workspaces: int = 0
    containers: int = 0
    images: int = 0
    archives: int = 0
    log_rows: int = 0
    metric_rows: int = 0
    tokens: int = 0
    errors: list[str] = field(default_factory=list)


class MaintenanceService:
    def __init__(
        self,
        settings: Settings,
        session_factory: SessionFactory,
        store: DeploymentStore,
        docker: DockerService,
        storage: Storage,
    ) -> None:
        self.settings = settings
        self.sessions = session_factory
        self.store = store
        self.docker = docker
        self.storage = storage

    # ------------------------------------------------------------------ reaper
    def reap_stale_deployments(self, now: datetime | None = None) -> int:
        """Fail deployments whose worker died (no heartbeat for STALE_DEPLOYMENT_MINUTES)."""
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(minutes=self.settings.stale_deployment_minutes)
        with self.sessions() as session:
            stale = list(
                session.scalars(
                    select(Deployment.id).where(
                        Deployment.status.in_(DeploymentStatus.active()),
                        or_(
                            Deployment.heartbeat_at < cutoff,
                            Deployment.heartbeat_at.is_(None) & (Deployment.created_at < cutoff),
                        ),
                    )
                )
            )
        for deployment_id in stale:
            self.store.transition(
                deployment_id,
                DeploymentStatus.FAILED,
                reason=FailureReason.TIMEOUT,
                error="The deployment stopped making progress (worker lost or queue unavailable)",
            )
            log.warning("stale_deployment_reaped", deployment_id=str(deployment_id))
        return len(stale)

    # ------------------------------------------------------------------ cleanup
    def cleanup(self, now: datetime | None = None) -> CleanupReport:
        now = now or datetime.now(UTC)
        report = CleanupReport()
        steps = (
            self._workspaces, self._orphan_containers, self._images_and_archives, self._rows,
        )  # fmt: skip
        for step in steps:
            try:
                step(report, now)
            except Exception as exc:
                log.exception("cleanup_step_failed", step=step.__name__)
                report.errors.append(f"{step.__name__}: {type(exc).__name__}")
        log.info("cleanup_finished", **{k: v for k, v in report.__dict__.items() if k != "errors"})
        return report

    def _active_ids(self) -> set[str]:
        with self.sessions() as session:
            return {
                str(i)
                for i in session.scalars(
                    select(Deployment.id).where(Deployment.status.in_(DeploymentStatus.active()))
                )
            }

    def _workspaces(self, report: CleanupReport, now: datetime) -> None:
        report.workspaces = self.storage.prune_workspaces(
            self.settings.workspace_max_age_minutes * 60, self._active_ids()
        )

    def _orphan_containers(self, report: CleanupReport, now: datetime) -> None:
        """Remove managed containers that no live deployment owns (crashes, failed cleanups)."""
        with self.sessions() as session:
            live = {
                str(i)
                for i in session.scalars(
                    select(Deployment.id).where(
                        Deployment.status.in_((*DeploymentStatus.active(), DeploymentStatus.RUNNING))
                    )
                )
            }
        for container in self.docker.list_managed_containers(include_stopped=True):
            owner = container.labels.get("paas.deployment_id")
            if owner and owner not in live:
                self.docker.remove_container(container.id)
                report.containers += 1

    def _images_and_archives(self, report: CleanupReport, now: datetime) -> None:
        keep_n = self.settings.image_retention_per_project
        with self.sessions() as session:
            projects = list(session.scalars(select(Project)))
            plans: list[tuple[Project, list[Deployment]]] = []
            for project in projects:
                deployments = list(
                    session.scalars(
                        select(Deployment)
                        .where(Deployment.project_id == project.id)
                        .order_by(Deployment.number.desc())
                    )
                )
                plans.append((project, deployments))

        for project, deployments in plans:
            succeeded = [d for d in deployments if d.deployed_at is not None and d.image_name]
            keep_images = {d.image_name for d in succeeded[:keep_n]}
            keep_images |= {
                d.image_name
                for d in deployments
                if d.status in (*DeploymentStatus.active(), DeploymentStatus.RUNNING)
            }
            if project.current_deployment_id:
                keep_images |= {d.image_name for d in deployments if d.id == project.current_deployment_id}
            for deployment in deployments:
                image = deployment.image_name
                if not image or image in keep_images or deployment.meta.get("image_pruned"):
                    continue
                try:
                    if self.docker.remove_image(image):
                        report.images += 1
                    self.store.merge_meta(deployment.id, {"image_pruned": True})
                except DockerError as exc:
                    report.errors.append(f"image {image}: {exc}")

            keep_archives = {d.source_archive for d in deployments[: keep_n + 1] if d.source_archive}
            if project.source_archive:
                keep_archives.add(project.source_archive)
            report.archives += self.storage.prune_archives(project.id, keep_archives)
        try:
            self.docker.prune_dangling_images()
        except DockerError as exc:
            report.errors.append(f"prune: {exc}")

    def _rows(self, report: CleanupReport, now: datetime) -> None:
        log_cutoff = now - timedelta(days=self.settings.log_retention_days)
        metric_cutoff = now - timedelta(hours=self.settings.metrics_retention_hours)
        with self.sessions() as session:
            report.log_rows = session.execute(
                delete(DeploymentLog).where(DeploymentLog.timestamp < log_cutoff)
            ).rowcount  # type: ignore[attr-defined]
            report.metric_rows = session.execute(
                delete(ProjectMetric).where(ProjectMetric.timestamp < metric_cutoff)
            ).rowcount  # type: ignore[attr-defined]
            report.tokens = session.execute(
                delete(RefreshToken).where(RefreshToken.expires_at < now)
            ).rowcount  # type: ignore[attr-defined]
