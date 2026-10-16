"""Development seed: a demo user and a demo project built from examples/static.

    python -m app.cli.seed [--no-deploy]

The demo password comes from SEED_DEMO_PASSWORD or is generated randomly and printed once.
A real deployment is queued (no fake data): it needs the worker and Docker to complete.
Refuses to run when ENVIRONMENT=production.
"""

from __future__ import annotations

import argparse
import io
import secrets
import sys
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.builds.workspace import Storage
from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import sync_session
from app.models import (
    AuditAction,
    AuditLog,
    Deployment,
    DeploymentStatus,
    DeploymentTrigger,
    Project,
    ProjectStatus,
    Runtime,
    SourceType,
    User,
)

EXAMPLE_CANDIDATES = (
    Path("/app/examples/static"),
    Path(__file__).resolve().parents[3] / "examples" / "static",
)


def _zip_directory(directory: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(directory).as_posix())
    return buffer.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-deploy", action="store_true", help="do not queue the demo deployment")
    args = parser.parse_args(argv)

    settings = get_settings()
    if settings.environment == "production":
        print("Refusing to seed a production environment.", file=sys.stderr)
        return 2

    example = next((p for p in EXAMPLE_CANDIDATES if p.is_dir()), None)
    if example is None:
        print("examples/static not found", file=sys.stderr)
        return 1

    generated = settings.seed_demo_password is None
    password = secrets.token_urlsafe(12) + "A1!" if generated else settings.seed_demo_password.get_secret_value()  # type: ignore[union-attr]
    storage = Storage(settings.data_dir)
    deployment_id: uuid.UUID | None = None

    with sync_session() as session:
        user = session.scalar(select(User).where(User.email == settings.seed_demo_email))
        if user is None:
            user = User(
                email=settings.seed_demo_email, name="Demo User", password_hash=hash_password(password)
            )
            session.add(user)
            session.flush()
            session.add(
                AuditLog(user_id=user.id, action=str(AuditAction.USER_REGISTERED), details={"seed": True})
            )
            print(f"Created demo user {user.email}")
            print(f"Password: {password}" if generated else "Password: (from SEED_DEMO_PASSWORD)")
        else:
            print(f"Demo user {user.email} already exists (password unchanged)")

        project = session.scalar(select(Project).where(Project.slug == "demo-static"))
        if project is None:
            project = Project(
                user_id=user.id,
                name="Demo static site",
                slug="demo-static",
                description="Seeded from examples/static",
                runtime=Runtime.AUTO,
                source_type=SourceType.ZIP,
                status=ProjectStatus.NEW,
                health_check_path="/",
                cpu_limit=settings.app_cpu_default,
                memory_limit_mb=min(128, settings.app_memory_max_mb),
            )
            session.add(project)
            session.flush()
            session.add(AuditLog(user_id=user.id, action=str(AuditAction.PROJECT_CREATED), resource_type="project",
                                 resource_id=str(project.id), details={"seed": True}))  # fmt: skip
            print(f"Created project {project.slug}")
        elif project.user_id != user.id:
            print("Slug demo-static belongs to another user; skipping project seed")
            return 0

        if not project.source_archive:
            path, relative = storage.new_archive_path(project.id)
            path.write_bytes(_zip_directory(example))
            project.source_archive = relative

        active = session.scalar(
            select(Deployment.id).where(
                Deployment.project_id == project.id, Deployment.status.in_(DeploymentStatus.active())
            )
        )
        if not args.no_deploy and active is None and project.status != ProjectStatus.RUNNING:
            project.deployment_counter += 1
            deployment = Deployment(
                id=uuid.uuid4(),
                project_id=project.id,
                number=project.deployment_counter,
                status=DeploymentStatus.QUEUED,
                trigger=DeploymentTrigger.UPLOAD,
                source_type=SourceType.ZIP,
                source_archive=project.source_archive,
                created_by_id=user.id,
                heartbeat_at=datetime.now(UTC),
                meta={"seed": True},
            )
            session.add(deployment)
            deployment_id = deployment.id

    if deployment_id is not None:
        from app.workers.celery_app import celery_app

        celery_app.send_task("deployments.run", args=[str(deployment_id)])
        print(f"Queued deployment {deployment_id} → http://demo-static.{settings.apps_base_domain}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
