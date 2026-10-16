"""DeploymentPipeline — runs one deployment end to end inside a Celery worker.

    QUEUED → BUILDING → DEPLOYING → HEALTH_CHECKING → RUNNING
             (rollbacks skip BUILDING and reuse the stored image)

Any failure moves the deployment to FAILED with a reason and message, removes the half
started container and leaves the previously serving version untouched.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import redis
import structlog

from app.builds.archive import remove_tree
from app.builds.build_service import BuildService, SourceError, SourceSpec
from app.builds.dockerfiles import DEFAULT_PORTS, READ_ONLY_RUNTIMES
from app.core.config import Settings
from app.core.crypto import SecretBoxError
from app.core.logging import get_logger
from app.deployments.health import HealthResult, Probe, http_probe, run_health_check
from app.deployments.naming import container_name, network_alias, network_name
from app.deployments.state_machine import InvalidTransition
from app.docker.service import (
    BuildCancelled,
    BuildFailed,
    BuildTimeout,
    ContainerSpec,
    DockerError,
    DockerService,
    DockerUnavailable,
)
from app.logs.sink import LogSink
from app.models import AuditAction, DeploymentStatus, FailureReason, LogLevel, LogSource, Runtime
from app.workers.locks import project_lock
from app.workers.store import DeploymentContext, DeploymentStore, SessionFactory

log = get_logger(__name__)

PLATFORM_ENV_PREFIX = "MINIPAAS_"


class StageFailure(Exception):
    def __init__(self, reason: FailureReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


class Cancelled(Exception):
    pass


@dataclass
class _Started:
    container_id: str
    network: str
    alias: str
    port: int


def effective_port(
    runtime: Runtime | None, environment: dict[str, str], project_port: int | None, default: int
) -> int:
    """Static sites always listen on 8080; otherwise PORT env > project setting > runtime default."""
    if runtime == Runtime.STATIC:
        return DEFAULT_PORTS[Runtime.STATIC]
    raw = environment.get("PORT", "").strip()
    if raw.isdigit() and 1 <= int(raw) <= 65535:
        return int(raw)
    return project_port or default


class DeploymentPipeline:
    def __init__(
        self,
        settings: Settings,
        docker: DockerService,
        builds: BuildService,
        store: DeploymentStore,
        session_factory: SessionFactory,
        redis_client: redis.Redis,
        probe: Probe = http_probe,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.docker = docker
        self.builds = builds
        self.store = store
        self.sessions = session_factory
        self.redis = redis_client
        self.probe = probe
        self.sleep = sleep

    # ------------------------------------------------------------------ entry point
    def run(self, deployment_id: uuid.UUID) -> DeploymentStatus | None:
        try:
            ctx = self.store.claim(deployment_id)
        except SecretBoxError:
            log.error("deployment_env_decrypt_failed", deployment_id=str(deployment_id))
            self.store.transition(
                deployment_id,
                DeploymentStatus.FAILED,
                reason=FailureReason.INTERNAL_ERROR,
                error="Environment variables could not be decrypted (check ENCRYPTION_KEYS)",
            )
            return DeploymentStatus.FAILED
        if ctx is None:
            log.info("deployment_skipped", deployment_id=str(deployment_id))
            return None
        structlog.contextvars.bind_contextvars(
            deployment_id=str(ctx.deployment_id), project_id=str(ctx.project_id)
        )
        sink = LogSink(
            self.sessions,
            self.redis,
            ctx.deployment_id,
            max_bytes=self.settings.max_log_bytes_per_deployment,
            used_bytes=ctx.log_bytes,
        )
        started: _Started | None = None
        workspace = self.builds.storage.workspace(ctx.deployment_id)
        log.info("deployment_started", number=ctx.number, trigger=str(ctx.trigger))
        try:
            sink.write("$ deployment started", source=LogSource.SYSTEM)
            if ctx.image_name:  # rollback: the image already exists
                image, runtime, default_port, read_only = self._reuse_image(ctx, sink)
            else:
                image, runtime, default_port, read_only = self._build(ctx, sink, workspace)
            self._check_cancel(ctx)
            started = self._deploy(ctx, sink, image, runtime, default_port, read_only)
            self._check_cancel(ctx)
            self._health_check(ctx, sink, started)
            self._go_live(ctx, sink)
            sink.info("Deployment successful.", source=LogSource.SYSTEM)
            log.info("deployment_succeeded", number=ctx.number)
            return DeploymentStatus.RUNNING
        except Cancelled:
            sink.warning("Deployment cancelled.", source=LogSource.SYSTEM)
            self._discard(started, sink)
            self._safe_transition(ctx, DeploymentStatus.CANCELLED)
            self.store.audit(ctx.user_id, AuditAction.DEPLOYMENT_CANCELLED, "deployment", ctx.deployment_id)
            return DeploymentStatus.CANCELLED
        except StageFailure as exc:
            return self._fail(ctx, sink, started, exc.reason, exc.message)
        except DockerUnavailable as exc:
            return self._fail(
                ctx, sink, started, FailureReason.INTERNAL_ERROR, f"Docker is unavailable: {exc}"
            )
        except Exception as exc:
            log.exception("deployment_internal_error")
            return self._fail(
                ctx, sink, started, FailureReason.INTERNAL_ERROR, f"Internal error: {type(exc).__name__}"
            )
        finally:
            remove_tree(workspace)
            sink.close()
            structlog.contextvars.unbind_contextvars("deployment_id", "project_id")

    # ------------------------------------------------------------------ stages
    def _check_cancel(self, ctx: DeploymentContext) -> None:
        if self.store.cancel_requested(ctx.deployment_id):
            raise Cancelled

    def _reuse_image(self, ctx: DeploymentContext, sink: LogSink) -> tuple[str, Runtime | None, int, bool]:
        assert ctx.image_name is not None
        self.store.transition(ctx.deployment_id, DeploymentStatus.DEPLOYING, runtime=ctx.rollback_runtime)
        sink.info(f"Rolling back: reusing image {ctx.image_name}")
        if not self.docker.image_exists(ctx.image_name):
            raise StageFailure(FailureReason.DEPLOY_FAILED, f"Image {ctx.image_name} no longer exists")
        runtime = ctx.rollback_runtime
        default = ctx.rollback_port or DEFAULT_PORTS.get(runtime or Runtime.DOCKERFILE, 8000)
        return ctx.image_name, runtime, default, runtime in READ_ONLY_RUNTIMES

    def _build(
        self, ctx: DeploymentContext, sink: LogSink, workspace: Path
    ) -> tuple[str, Runtime, int, bool]:
        self.store.transition(ctx.deployment_id, DeploymentStatus.BUILDING)

        def say(line: str) -> None:
            sink.info(line, source=LogSource.BUILD)

        remove_tree(workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        try:
            commit = self.builds.prepare_source(
                SourceSpec(ctx.source_type, ctx.source_archive, ctx.repository_url, ctx.branch),
                workspace,
                say,
            )
            plan, reason = self.builds.plan(workspace, ctx.requested_runtime, ctx.start_command, say)
        except SourceError as exc:
            raise StageFailure(FailureReason.BUILD_FAILED, str(exc)) from exc
        self.store.update(ctx.deployment_id, runtime=plan.runtime, commit_hash=commit)
        self.store.merge_meta(
            ctx.deployment_id,
            {"runtime_detection": reason, "start_command": plan.start_command, "dockerfile": plan.dockerfile},
        )
        self._check_cancel(ctx)

        say("Building Docker image...")
        build_started = time.monotonic()
        try:
            tag, image_id = self.builds.build_project(
                project_id=ctx.project_id,
                deployment_id=ctx.deployment_id,
                number=ctx.number,
                source_path=workspace,
                plan=plan,
                on_line=lambda line: sink.write(line, source=LogSource.BUILD, level=LogLevel.INFO),
                should_cancel=lambda: self.store.cancel_requested(ctx.deployment_id),
            )
        except BuildCancelled as exc:
            raise Cancelled from exc
        except BuildTimeout as exc:
            raise StageFailure(FailureReason.TIMEOUT, str(exc)) from exc
        except BuildFailed as exc:
            raise StageFailure(FailureReason.BUILD_FAILED, str(exc)) from exc
        build_seconds = round(time.monotonic() - build_started, 1)
        say(f"Image created: {tag} ({build_seconds}s)")
        self.store.update(ctx.deployment_id, image_name=tag)
        self.store.merge_meta(ctx.deployment_id, {"image_id": image_id, "build_seconds": build_seconds})
        self.store.transition(ctx.deployment_id, DeploymentStatus.DEPLOYING)
        return tag, plan.runtime, plan.default_port, plan.read_only_rootfs

    def _deploy(
        self,
        ctx: DeploymentContext,
        sink: LogSink,
        image: str,
        runtime: Runtime | None,
        default_port: int,
        read_only: bool,
    ) -> _Started:
        port = effective_port(runtime, ctx.environment, ctx.port, default_port)
        network = network_name(ctx.project_id)
        alias = network_alias(ctx.deployment_id)
        name = container_name(ctx.project_id, ctx.deployment_id)
        environment = {k: v for k, v in ctx.environment.items() if not k.startswith(PLATFORM_ENV_PREFIX)}
        environment.update(
            {
                "PORT": str(port),
                "MINIPAAS_PROJECT": ctx.slug,
                "MINIPAAS_DEPLOYMENT": str(ctx.number),
            }
        )
        cpu = min(ctx.cpu_limit, self.settings.app_cpu_max)
        memory = min(ctx.memory_limit_mb, self.settings.app_memory_max_mb)
        sink.info(f"Starting container (port {port}, {cpu:g} CPU, {memory} MB RAM)...")
        try:
            with project_lock(self.redis, ctx.project_id):
                self.docker.ensure_network(
                    network, {"paas.managed": "true", "paas.project_id": str(ctx.project_id)}
                )
                self.docker.connect_network(self.settings.proxy_container_name, network)
                self.docker.remove_container(name)  # leftovers from a crashed attempt
                container_id = self.docker.create_container(
                    ContainerSpec(
                        name=name,
                        image=image,
                        network=network,
                        alias=alias,
                        environment=environment,
                        labels={
                            "paas.project_id": str(ctx.project_id),
                            "paas.deployment_id": str(ctx.deployment_id),
                            "paas.user_id": str(ctx.user_id),
                        },
                        cpu_limit=cpu,
                        memory_mb=memory,
                        pids_limit=self.settings.app_pids_limit,
                        read_only=read_only,
                        tmpfs_mb=self.settings.app_tmpfs_mb,
                    )
                )
                self.store.update(
                    ctx.deployment_id, container_id=container_id, container_name=name, internal_port=port
                )
                self.docker.start_container(container_id)
        except DockerUnavailable:
            raise
        except DockerError as exc:
            raise StageFailure(FailureReason.DEPLOY_FAILED, str(exc)) from exc
        sink.info("Container started.")
        return _Started(container_id=container_id, network=network, alias=alias, port=port)

    def _health_check(self, ctx: DeploymentContext, sink: LogSink, started: _Started) -> None:
        self.store.transition(ctx.deployment_id, DeploymentStatus.HEALTH_CHECKING)
        path = ctx.health_check_path if ctx.health_check_path.startswith("/") else f"/{ctx.health_check_path}"
        url = f"http://{started.alias}:{started.port}{path}"
        sink.info(
            f"Health check: GET {path} on port {started.port} (timeout {self.settings.health_check_timeout_seconds}s)..."
        )
        joined = None
        try:
            joined = self.docker.connect_self(started.network)
            result: HealthResult = run_health_check(
                url,
                container_state=lambda: self.docker.get_container_status(started.container_id),
                timeout_seconds=self.settings.health_check_timeout_seconds,
                interval_seconds=self.settings.health_check_interval_seconds,
                probe=self.probe,
                sleep=self.sleep,
                on_attempt=lambda n, err: sink.write(
                    f"Health check attempt {n}: {err}", level=LogLevel.DEBUG, source=LogSource.DEPLOY
                ),
                should_cancel=lambda: self.store.cancel_requested(ctx.deployment_id),
            )
        finally:
            if joined:
                self.docker.disconnect_network(joined, started.network)
        if result.error == "cancelled":
            raise Cancelled
        self.store.update(ctx.deployment_id, health_check=result.as_dict())
        if not result.ok:
            raise StageFailure(FailureReason.HEALTH_CHECK_FAILED, result.error or "Health check failed")
        sink.info(
            f"Health check passed (HTTP {result.status_code} after {result.attempts} attempt(s), {result.duration_ms} ms)."
        )

    def _go_live(self, ctx: DeploymentContext, sink: LogSink) -> None:
        with project_lock(self.redis, ctx.project_id):
            previous = self.store.go_live(ctx.deployment_id)
            sink.info("Routing traffic to the new deployment...")
            if previous and previous.container_id:
                # Let the proxy pick up the new route before the old container disappears.
                self.sleep(self.settings.route_propagation_seconds)
                try:
                    self.docker.stop_container(previous.container_id, timeout=10)
                    self.docker.remove_container(previous.container_id)
                    sink.info("Previous deployment stopped.")
                except DockerError as exc:
                    sink.warning(f"Could not stop the previous container: {exc}")

    # ------------------------------------------------------------------ failure handling
    def _discard(self, started: _Started | None, sink: LogSink) -> None:
        if started is None:
            return
        try:
            tail = self.docker.get_container_logs(started.container_id, tail=50)
            if tail:
                sink.warning("Last application output:", source=LogSource.RUNTIME)
                for line in tail:
                    sink.write(line, level=LogLevel.INFO, source=LogSource.RUNTIME)
            self.docker.remove_container(started.container_id)
        except DockerError as exc:
            log.warning("discard_container_failed", error=str(exc))

    def _safe_transition(self, ctx: DeploymentContext, target: DeploymentStatus, **kwargs: object) -> None:
        try:
            self.store.transition(ctx.deployment_id, target, **kwargs)  # type: ignore[arg-type]
        except InvalidTransition as exc:
            log.warning("terminal_transition_skipped", error=str(exc))

    def _fail(
        self,
        ctx: DeploymentContext,
        sink: LogSink,
        started: _Started | None,
        reason: FailureReason,
        message: str,
    ) -> DeploymentStatus:
        log.warning("deployment_failed", reason=str(reason), error=message)
        sink.error(f"{reason}: {message}", source=LogSource.SYSTEM)
        self._discard(started, sink)
        self._safe_transition(ctx, DeploymentStatus.FAILED, reason=reason, error=message)
        self.store.audit(
            ctx.user_id, AuditAction.DEPLOYMENT_FAILED, "deployment", ctx.deployment_id,
            {"reason": str(reason), "message": message[:500]},
        )  # fmt: skip
        return DeploymentStatus.FAILED
