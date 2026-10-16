"""DockerService — the only module that talks to the Docker daemon.

* Runtime operations use the Docker SDK (low-level API for precise control).
* Image builds use BuildKit through the docker CLI (`docker buildx build`), optionally on an
  isolated `docker-container` builder that has its own CPU/memory caps and network.

Every user container is created with the hardened profile in `_host_config()`.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import docker
import docker.errors
from docker.types import LogConfig, Ulimit

from app.core.logging import get_logger
from app.docker.stats import ContainerStats, parse_docker_time, parse_stats

log = get_logger(__name__)

MANAGED_LABEL = "paas.managed"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class DockerError(Exception):
    """A Docker operation failed."""


class DockerUnavailable(DockerError):
    """The Docker daemon cannot be reached."""


class BuildFailed(DockerError):
    pass


class BuildCancelled(DockerError):
    pass


class BuildTimeout(DockerError):
    pass


@dataclass(frozen=True)
class ContainerSpec:
    name: str
    image: str
    network: str
    alias: str
    environment: dict[str, str]
    labels: dict[str, str]
    cpu_limit: float
    memory_mb: int
    pids_limit: int
    read_only: bool
    tmpfs_mb: int
    user: str | None = None


@dataclass(frozen=True)
class ContainerState:
    id: str
    name: str
    status: str
    running: bool
    restarting: bool
    exit_code: int | None
    oom_killed: bool
    started_at: datetime | None
    finished_at: datetime | None
    restart_count: int
    labels: dict[str, str] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class BuildOptions:
    timeout_seconds: int
    use_isolated_builder: bool = True
    builder_name: str = "paas-builder"
    builder_network: str = "paas-build"
    builder_cpu: float = 1.0
    builder_memory_mb: int = 1024


def _state_from_inspect(data: dict[str, Any]) -> ContainerState:
    state = data.get("State") or {}
    return ContainerState(
        id=data["Id"],
        name=str(data.get("Name", "")).lstrip("/"),
        status=str(state.get("Status", "unknown")),
        running=bool(state.get("Running")),
        restarting=bool(state.get("Restarting")),
        exit_code=state.get("ExitCode"),
        oom_killed=bool(state.get("OOMKilled")),
        started_at=parse_docker_time(state.get("StartedAt")),
        finished_at=parse_docker_time(state.get("FinishedAt")),
        restart_count=int(data.get("RestartCount") or 0),
        labels=(data.get("Config") or {}).get("Labels") or {},
        error=state.get("Error") or None,
    )


class DockerService:
    def __init__(
        self,
        client_factory: Callable[[], docker.DockerClient] | None = None,
        docker_cli: str | list[str] = "docker",
    ) -> None:
        self._client_factory = client_factory or (lambda: docker.from_env(timeout=60))
        self._client: docker.DockerClient | None = None
        self._lock = threading.Lock()
        # A command prefix: ["docker"] in production, a fake CLI script in tests.
        self._docker_cli = [docker_cli] if isinstance(docker_cli, str) else list(docker_cli)

    # ------------------------------------------------------------------ connection
    @property
    def client(self) -> docker.DockerClient:
        with self._lock:
            if self._client is None:
                try:
                    self._client = self._client_factory()
                except docker.errors.DockerException as exc:
                    raise DockerUnavailable(f"Cannot connect to the Docker daemon: {exc}") from exc
            return self._client

    @property
    def api(self) -> Any:
        return self.client.api

    def _call(self, operation: str, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except docker.errors.NotFound:
            raise
        except docker.errors.APIError as exc:
            raise DockerError(f"{operation} failed: {exc.explanation or exc}") from exc
        except docker.errors.DockerException as exc:
            raise DockerUnavailable(f"{operation} failed, Docker unavailable: {exc}") from exc
        except OSError as exc:  # socket errors surface as requests/urllib3 OSErrors
            raise DockerUnavailable(f"{operation} failed, Docker unavailable: {exc}") from exc

    def ping(self) -> bool:
        try:
            return bool(self._call("ping", self.client.ping))
        except DockerError:
            return False

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    # ------------------------------------------------------------------ images
    def _build_env(self) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": "/tmp",
        }
        for key in ("DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
            if os.environ.get(key):
                env[key] = os.environ[key]
        env["BUILDKIT_PROGRESS"] = "plain"
        return env

    def _run_cli(self, *args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*self._docker_cli, *args],
            env=self._build_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def ensure_builder(self, options: BuildOptions) -> str | None:
        """Create (once) an isolated BuildKit builder with resource caps. Returns its name,
        or None to fall back to the daemon's default builder."""
        if not options.use_isolated_builder:
            return None
        try:
            if self._run_cli("buildx", "inspect", options.builder_name).returncode == 0:
                return options.builder_name
            self.ensure_network(options.builder_network, {MANAGED_LABEL: "true"})
            cpu_quota = int(options.builder_cpu * 100_000)
            created = self._run_cli(
                "buildx", "create",
                "--name", options.builder_name,
                "--driver", "docker-container",
                "--driver-opt", f"network={options.builder_network}",
                "--driver-opt", f"memory={options.builder_memory_mb}m",
                "--driver-opt", f"cpu-quota={cpu_quota}",
                "--driver-opt", "cpu-period=100000",
                "--bootstrap",
                timeout=300,
            )  # fmt: skip
        except (OSError, subprocess.TimeoutExpired, DockerError) as exc:
            log.warning("isolated_builder_unavailable", error=str(exc))
            return None
        if created.returncode != 0:
            log.warning("isolated_builder_unavailable", error=created.stderr.strip()[-500:])
            return None
        return options.builder_name

    def build_image(
        self,
        context_dir: str,
        dockerfile: str,
        tag: str,
        labels: dict[str, str],
        options: BuildOptions,
        on_line: Callable[[str], None],
        should_cancel: Callable[[], bool] = lambda: False,
    ) -> str:
        """Build `tag` from `context_dir`. Streams output lines to `on_line`. Returns image id."""
        builder = self.ensure_builder(options)
        args = [*self._docker_cli, "buildx", "build", "--progress=plain", "--load"]
        if builder:
            args += ["--builder", builder]
        args += ["-f", os.path.join(context_dir, dockerfile), "-t", tag]
        for key, value in labels.items():
            args += ["--label", f"{key}={value}"]
        args.append(context_dir)

        try:
            proc = subprocess.Popen(
                args,
                env=self._build_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            raise DockerUnavailable(f"Cannot run the docker CLI: {exc}") from exc

        outcome: dict[str, str] = {}

        def watchdog() -> None:
            deadline = time.monotonic() + options.timeout_seconds
            while proc.poll() is None:
                if time.monotonic() > deadline:
                    outcome["reason"] = "timeout"
                    proc.kill()
                    return
                if should_cancel():
                    outcome["reason"] = "cancelled"
                    proc.kill()  # closing the BuildKit session cancels the build
                    return
                time.sleep(1)

        threading.Thread(target=watchdog, name="build-watchdog", daemon=True).start()
        tail: list[str] = []
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = _ANSI_RE.sub("", raw.rstrip("\n"))
            if not line.strip():
                continue
            tail = [*tail[-19:], line]
            on_line(line)
        code = proc.wait()

        if outcome.get("reason") == "timeout":
            raise BuildTimeout(f"Build exceeded {options.timeout_seconds}s and was stopped")
        if outcome.get("reason") == "cancelled":
            raise BuildCancelled("Build cancelled by user")
        if code != 0:
            hint = next((t for t in reversed(tail) if "ERROR" in t or "error" in t), tail[-1] if tail else "")
            if "Cannot connect to the Docker daemon" in hint or "docker.sock" in hint:
                raise DockerUnavailable(hint)
            raise BuildFailed(f"docker build exited with code {code}: {hint[:500]}")
        try:
            image = self._call("inspect image", self.api.inspect_image, tag)
        except docker.errors.NotFound as exc:
            raise BuildFailed("Build finished but the image was not found") from exc
        return str(image["Id"])

    def image_exists(self, tag: str) -> bool:
        try:
            self._call("inspect image", self.api.inspect_image, tag)
            return True
        except docker.errors.NotFound:
            return False

    def remove_image(self, tag: str) -> bool:
        try:
            self._call("remove image", self.api.remove_image, tag, force=False, noprune=False)
            return True
        except docker.errors.NotFound:
            return False

    def list_project_images(self, project_id: str) -> list[dict[str, Any]]:
        images = self._call(
            "list images", self.api.images, filters={"label": f"paas.project_id={project_id}"}
        )
        return list(images or [])

    def prune_dangling_images(self) -> None:
        self._call("prune images", self.api.prune_images, filters={"dangling": True, "label": MANAGED_LABEL})

    # ------------------------------------------------------------------ networks
    def _find_network(self, name: str) -> dict[str, Any] | None:
        for net in self._call("list networks", self.api.networks, names=[name]) or []:
            if net.get("Name") == name:  # the names filter matches substrings
                return dict(net)
        return None

    def ensure_network(self, name: str, labels: dict[str, str]) -> str:
        existing = self._find_network(name)
        if existing:
            return str(existing["Id"])
        try:
            created = self._call(
                "create network", self.api.create_network, name, driver="bridge", labels=labels
            )
        except DockerError:
            existing = self._find_network(name)  # lost a creation race
            if existing:
                return str(existing["Id"])
            raise
        return str(created["Id"])

    def remove_network(self, name: str) -> None:
        net = self._find_network(name)
        if net is None:
            return
        for container_id in (
            self._call("inspect network", self.api.inspect_network, net["Id"]).get("Containers") or {}
        ):
            self.disconnect_network(container_id, name)
        with contextlib.suppress(docker.errors.NotFound):
            self._call("remove network", self.api.remove_network, net["Id"])

    def connect_network(self, container: str, network: str, aliases: list[str] | None = None) -> None:
        try:
            self._call(
                "connect network",
                self.api.connect_container_to_network,
                container,
                network,
                aliases=aliases,
            )
        except docker.errors.NotFound as exc:
            raise DockerError(f"Cannot attach {container} to {network}: not found") from exc
        except DockerError as exc:
            if "already exists" in str(exc) or "already attached" in str(exc):
                return
            raise

    def disconnect_network(self, container: str, network: str) -> None:
        try:
            self._call(
                "disconnect network",
                self.api.disconnect_container_from_network,
                container,
                network,
                force=True,
            )
        except (docker.errors.NotFound, DockerError) as exc:
            log.debug("network_disconnect_ignored", container=container, network=network, error=str(exc))

    def container_networks(self, container: str) -> list[str]:
        data = self._call("inspect container", self.api.inspect_container, container)
        return list(((data.get("NetworkSettings") or {}).get("Networks") or {}).keys())

    # ------------------------------------------------------------------ containers
    def _host_config(self, spec: ContainerSpec) -> Any:
        tmpfs_opts = f"rw,noexec,nosuid,nodev,size={spec.tmpfs_mb}m"
        return self.api.create_host_config(
            network_mode=spec.network,
            mem_limit=f"{spec.memory_mb}m",
            memswap_limit=f"{spec.memory_mb}m",  # no swap beyond the memory limit
            nano_cpus=int(spec.cpu_limit * 1_000_000_000),
            pids_limit=spec.pids_limit,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            read_only=spec.read_only,
            tmpfs={"/tmp": tmpfs_opts},
            init=True,
            ulimits=[Ulimit(name="nofile", soft=4096, hard=8192)],
            restart_policy={"Name": "on-failure", "MaximumRetryCount": 3},
            log_config=LogConfig(type="json-file", config={"max-size": "10m", "max-file": "2"}),
            privileged=False,
            publish_all_ports=False,
        )

    def create_container(self, spec: ContainerSpec) -> str:
        networking = self.api.create_networking_config(
            {spec.network: self.api.create_endpoint_config(aliases=[spec.alias])}
        )
        created = self._call(
            "create container",
            self.api.create_container,
            image=spec.image,
            name=spec.name,
            environment=spec.environment,
            labels={MANAGED_LABEL: "true", **spec.labels},
            host_config=self._host_config(spec),
            networking_config=networking,
            user=spec.user,
            detach=True,
        )
        return str(created["Id"])

    def start_container(self, container: str) -> None:
        self._call("start container", self.api.start, container)

    def stop_container(self, container: str, timeout: int = 10) -> None:
        with contextlib.suppress(docker.errors.NotFound):
            self._call("stop container", self.api.stop, container, timeout=timeout)

    def restart_container(self, container: str, timeout: int = 10) -> None:
        self._call("restart container", self.api.restart, container, timeout=timeout)

    def remove_container(self, container: str) -> None:
        with contextlib.suppress(docker.errors.NotFound):
            self._call("remove container", self.api.remove_container, container, force=True, v=True)

    def get_container_status(self, container: str) -> ContainerState | None:
        try:
            return _state_from_inspect(self._call("inspect container", self.api.inspect_container, container))
        except docker.errors.NotFound:
            return None

    def get_container_logs(self, container: str, tail: int = 100) -> list[str]:
        try:
            raw = self._call(
                "container logs",
                self.api.logs,
                container,
                stdout=True,
                stderr=True,
                tail=tail,
                timestamps=False,
            )
        except docker.errors.NotFound:
            return []
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        return [line for line in text.splitlines() if line.strip()]

    def stream_container_logs(self, container: str, since: int | None = None) -> Iterator[bytes]:
        kwargs: dict[str, Any] = {"stream": True, "follow": True, "timestamps": True}
        if since:
            kwargs["since"] = since
        return self._call("stream logs", self.api.logs, container, stdout=True, stderr=True, **kwargs)  # type: ignore[no-any-return]

    def get_container_stats(self, container: str) -> ContainerStats:
        raw = self._call("container stats", self.api.stats, container, stream=False)
        return parse_stats(raw)

    def list_managed_containers(self, *, include_stopped: bool = False) -> list[ContainerState]:
        items = self._call(
            "list containers",
            self.api.containers,
            all=include_stopped,
            filters={"label": f"{MANAGED_LABEL}=true"},
        )
        states = []
        for item in items or []:
            state = self.get_container_status(item["Id"])
            if state is not None:
                states.append(state)
        return states

    def events(self, since: int | None = None) -> Iterator[dict[str, Any]]:
        return self._call(  # type: ignore[no-any-return]
            "docker events",
            self.client.events,
            decode=True,
            since=since,
            filters={"type": "container", "label": f"{MANAGED_LABEL}=true"},
        )

    def connect_self(self, network: str) -> str | None:
        """Attach the current container (the worker) to a network, for health checks.

        Returns the container identifier, or None when not running inside Docker."""
        me = os.environ.get("WORKER_CONTAINER_NAME") or (
            os.environ.get("HOSTNAME") if os.path.exists("/.dockerenv") else None
        )
        if not me:
            return None
        self.connect_network(me, network)
        return me
