"""Runtime agent: one long-running process next to the Docker daemon.

Responsibilities
  * follow stdout/stderr of every managed container into the deployment logs,
  * sample `docker stats` and store/publish metrics,
  * detect crashed containers (after Docker's restart policy gives up),
  * reconcile: re-attach the proxy to project networks, follow new containers, and fail
    deployments whose container vanished.
"""

from __future__ import annotations

import signal
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import redis
from sqlalchemy import insert, select

from app.agent.log_follower import LogFollower
from app.core.config import Settings
from app.core.logging import get_logger
from app.deployments import events
from app.deployments.naming import network_name
from app.docker.service import ContainerState, DockerError, DockerService
from app.models import Deployment, DeploymentStatus, ProjectMetric
from app.services.heartbeats import beat
from app.workers.store import DeploymentStore, SessionFactory

log = get_logger(__name__)

CRASH_CHECK_DELAY = 5.0
RECONCILE_INTERVAL = 30.0


@dataclass(frozen=True)
class MetricSample:
    row: dict[str, Any]
    event: dict[str, Any]
    user_id: str


class Agent:
    def __init__(
        self,
        settings: Settings,
        docker: DockerService,
        store: DeploymentStore,
        sessions: SessionFactory,
        redis_client: redis.Redis,
    ) -> None:
        self.settings = settings
        self.docker = docker
        self.store = store
        self.sessions = sessions
        self.redis = redis_client
        self.stop_event = threading.Event()
        self.followers: dict[str, LogFollower] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="stats")
        self._suspects: set[str] = set()

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _deployment_id(state: ContainerState) -> uuid.UUID | None:
        raw = state.labels.get("paas.deployment_id")
        try:
            return uuid.UUID(raw) if raw else None
        except ValueError:
            return None

    def _loop(self, interval: float, func: Callable[[], None], name: str) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                func()
            except Exception:
                log.exception("agent_loop_failed", loop=name)
            self.stop_event.wait(max(0.5, interval - (time.monotonic() - started)))

    # ------------------------------------------------------------------ logs
    def ensure_follower(self, state: ContainerState) -> None:
        deployment_id = self._deployment_id(state)
        if deployment_id is None or not state.running:
            return
        with self._lock:
            existing = self.followers.get(state.id)
            if existing is not None and existing.is_alive():
                return
            follower = LogFollower(
                self.docker, self.sessions, self.redis, state.id, deployment_id,
                max_bytes=self.settings.max_log_bytes_per_deployment,
                lines_per_second=self.settings.max_runtime_log_lines_per_second,
            )  # fmt: skip
            self.followers[state.id] = follower
        follower.start()

    def _prune_followers(self) -> None:
        with self._lock:
            for container_id in [cid for cid, f in self.followers.items() if not f.is_alive()]:
                del self.followers[container_id]

    # ------------------------------------------------------------------ events / crashes
    def watch_events(self) -> None:
        backoff = 1.0
        while not self.stop_event.is_set():
            try:
                for event in self.docker.events(since=int(time.time())):
                    if self.stop_event.is_set():
                        break
                    backoff = 1.0
                    self._on_event(event)
            except DockerError as exc:
                log.warning("docker_events_disconnected", error=str(exc))
            except Exception:
                log.exception("docker_events_failed")
            self.stop_event.wait(backoff)
            backoff = min(backoff * 2, 30.0)

    def _on_event(self, event: dict[str, object]) -> None:
        action = str(event.get("Action") or event.get("status") or "")
        container_id = str(event.get("id") or "")
        if not container_id:
            return
        if action == "start":
            state = self.docker.get_container_status(container_id)
            if state is not None:
                self.ensure_follower(state)
        elif action == "die":
            timer = threading.Timer(CRASH_CHECK_DELAY, self.check_crash, args=(container_id,))
            timer.daemon = True
            timer.start()

    def check_crash(self, container_id: str) -> None:
        """Called a few seconds after `die`: if Docker is not restarting it, it crashed."""
        state = self.docker.get_container_status(container_id)
        if state is not None and (state.running or state.restarting):
            return
        deployment_id = self._deployment_id(state) if state else self._deployment_for_container(container_id)
        if deployment_id is None:
            return
        if state is None:
            message = "Container disappeared"
        else:
            message = f"Container exited with code {state.exit_code}"
            if state.oom_killed:
                message += " (killed: out of memory)"
            if state.restart_count:
                message += f" after {state.restart_count} restart(s)"
        if self.store.mark_crashed(deployment_id, message):
            log.warning("deployment_crashed", deployment_id=str(deployment_id), reason=message)

    def _deployment_for_container(self, container_id: str) -> uuid.UUID | None:
        with self.sessions() as session:
            return session.scalar(select(Deployment.id).where(Deployment.container_id == container_id))

    # ------------------------------------------------------------------ metrics
    def collect_metrics(self) -> None:
        containers = [
            c for c in self.docker.list_managed_containers() if c.running and self._deployment_id(c)
        ]
        if not containers:
            return
        samples = [s for s in self._pool.map(self._sample, containers) if s is not None]
        if not samples:
            return
        with self.sessions() as session:
            session.execute(insert(ProjectMetric), [s.row for s in samples])
        for sample in samples:
            if sample.user_id:
                events.publish(
                    self.redis, events.user_channel(sample.user_id), "metrics.sample", sample.event
                )

    def _sample(self, state: ContainerState) -> MetricSample | None:
        try:
            stats = self.docker.get_container_stats(state.id)
        except DockerError:
            return None
        now = datetime.now(UTC)
        uptime = int((now - state.started_at).total_seconds()) if state.started_at else 0
        project_id = state.labels.get("paas.project_id")
        row: dict[str, Any] = {
            "project_id": uuid.UUID(project_id) if project_id else None,
            "deployment_id": self._deployment_id(state),
            "timestamp": now,
            "cpu_usage": stats.cpu_percent,
            "memory_usage": stats.memory_usage,
            "memory_limit": stats.memory_limit,
            "network_rx": stats.network_rx,
            "network_tx": stats.network_tx,
            "uptime_seconds": max(uptime, 0),
            "restart_count": state.restart_count,
        }
        if row["project_id"] is None:
            return None
        event = {k: (str(v) if isinstance(v, uuid.UUID | datetime) else v) for k, v in row.items()}
        return MetricSample(row=row, event=event, user_id=state.labels.get("paas.user_id", ""))

    # ------------------------------------------------------------------ reconcile
    def reconcile(self) -> None:
        beat(self.redis, "agent")
        running = [c for c in self.docker.list_managed_containers() if c.running]
        for state in running:
            self.ensure_follower(state)
            project_id = state.labels.get("paas.project_id")
            if project_id:
                try:
                    self.docker.connect_network(
                        self.settings.proxy_container_name, network_name(uuid.UUID(project_id))
                    )
                except (DockerError, ValueError) as exc:
                    log.warning("proxy_reattach_failed", project_id=project_id, error=str(exc))
        self._prune_followers()

        running_ids = {c.id for c in running}
        with self.sessions() as session:
            expected = list(
                session.execute(
                    select(Deployment.id, Deployment.container_id).where(
                        Deployment.status == DeploymentStatus.RUNNING, Deployment.container_id.is_not(None)
                    )
                )
            )
        # Require two consecutive misses so a container mid-restart is not declared crashed.
        missing = {cid for _, cid in expected if cid and cid not in running_ids}
        for container_id in missing & self._suspects:
            self.check_crash(container_id)
        self._suspects = missing

    # ------------------------------------------------------------------ lifecycle
    def run(self) -> None:
        log.info("agent_starting")
        threads = [
            threading.Thread(target=self.watch_events, name="events", daemon=True),
            threading.Thread(target=self._loop, args=(self.settings.metrics_interval_seconds, self.collect_metrics, "metrics"), name="metrics", daemon=True),
            threading.Thread(target=self._loop, args=(RECONCILE_INTERVAL, self.reconcile, "reconcile"), name="reconcile", daemon=True),
        ]  # fmt: skip
        for thread in threads:
            thread.start()
        self.stop_event.wait()
        log.info("agent_stopping")
        with self._lock:
            followers = list(self.followers.values())
        for follower in followers:
            follower.stop()
        for follower in followers:
            follower.join(timeout=3)
        self._pool.shutdown(wait=False, cancel_futures=True)
        self.docker.close()
        log.info("agent_stopped")

    def install_signal_handlers(self) -> None:
        def handler(signum: int, _frame: object) -> None:
            log.info("agent_signal", signal=signum)
            self.stop_event.set()

        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)
