"""An in-memory DockerService stand-in for exercising the worker without a daemon."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from app.docker.service import BuildFailed, BuildOptions, ContainerSpec, ContainerState
from app.docker.stats import ContainerStats


class FakeDocker:
    def __init__(self) -> None:
        self.images: set[str] = set()
        self.containers: dict[str, dict[str, Any]] = {}
        self.networks: set[str] = set()
        self.attachments: set[tuple[str, str]] = set()
        self.events_log: list[str] = []
        self.build_output = ["#1 [internal] load build definition", "#2 DONE 0.2s"]
        self.fail_build: str | None = None
        self.exit_on_start: int | None = None
        self.removed_images: list[str] = []
        self._ids = itertools.count(1)

    # images
    def build_image(
        self, context_dir: str, dockerfile: str, tag: str, labels: dict[str, str], options: BuildOptions,
        on_line: Callable[[str], None], should_cancel: Callable[[], bool] = lambda: False,
    ) -> str:  # fmt: skip
        self.events_log.append(f"build:{tag}:{dockerfile}")
        for line in self.build_output:
            on_line(line)
        if self.fail_build:
            raise BuildFailed(self.fail_build)
        self.images.add(tag)
        return f"sha256:{abs(hash(tag)):x}"

    def image_exists(self, tag: str) -> bool:
        return tag in self.images

    def remove_image(self, tag: str) -> bool:
        self.removed_images.append(tag)
        if tag in self.images:
            self.images.discard(tag)
            return True
        return False

    def list_project_images(self, project_id: str) -> list[dict[str, Any]]:
        return [{"Id": t, "RepoTags": [t]} for t in self.images if project_id in t]

    def prune_dangling_images(self) -> None:
        pass

    # networks
    def ensure_network(self, name: str, labels: dict[str, str]) -> str:
        self.networks.add(name)
        return name

    def remove_network(self, name: str) -> None:
        self.networks.discard(name)
        self.attachments = {a for a in self.attachments if a[1] != name}

    def connect_network(self, container: str, network: str, aliases: list[str] | None = None) -> None:
        self.attachments.add((container, network))

    def disconnect_network(self, container: str, network: str) -> None:
        self.attachments.discard((container, network))

    def connect_self(self, network: str) -> str | None:
        self.attachments.add(("worker", network))
        return "worker"

    # containers
    def create_container(self, spec: ContainerSpec) -> str:
        container_id = f"c{next(self._ids)}"
        self.containers[container_id] = {"spec": spec, "running": False, "exit_code": None, "restarts": 0}
        self.events_log.append(f"create:{spec.name}")
        return container_id

    def start_container(self, container: str) -> None:
        entry = self.containers[container]
        if self.exit_on_start is not None:
            entry.update(running=False, exit_code=self.exit_on_start)
        else:
            entry.update(running=True, started=datetime.now(UTC))
        self.events_log.append(f"start:{container}")

    def stop_container(self, container: str, timeout: int = 10) -> None:
        if container in self.containers:
            self.containers[container].update(running=False, exit_code=0)
        self.events_log.append(f"stop:{container}")

    def restart_container(self, container: str, timeout: int = 10) -> None:
        self.containers[container]["restarts"] += 1
        self.events_log.append(f"restart:{container}")

    def remove_container(self, container: str) -> None:
        self.containers.pop(container, None)
        for cid, entry in list(self.containers.items()):
            if entry["spec"].name == container:
                self.containers.pop(cid)
        self.events_log.append(f"remove:{container}")

    def get_container_status(self, container: str) -> ContainerState | None:
        entry = self.containers.get(container)
        if entry is None:
            return None
        return ContainerState(
            id=container, name=entry["spec"].name, status="running" if entry["running"] else "exited",
            running=entry["running"], restarting=False, exit_code=entry["exit_code"], oom_killed=False,
            started_at=entry.get("started"), finished_at=None, restart_count=entry["restarts"],
            labels={"paas.managed": "true", **entry["spec"].labels},
        )  # fmt: skip

    def get_container_logs(self, container: str, tail: int = 100) -> list[str]:
        return ["Traceback (most recent call last):", "ModuleNotFoundError: No module named 'x'"]

    def list_managed_containers(self, *, include_stopped: bool = False) -> list[ContainerState]:
        states = [self.get_container_status(cid) for cid in self.containers]
        return [s for s in states if s is not None and (include_stopped or s.running)]

    def get_container_stats(self, container: str) -> ContainerStats:
        return ContainerStats(cpu_percent=12.5, memory_usage=100 * 2**20, memory_limit=256 * 2**20,
                              network_rx=2048, network_tx=1024)  # fmt: skip

    def stream_container_logs(self, container: str, since: int | None = None) -> Iterator[bytes]:
        yield b"2026-10-05T12:00:00.000000000Z Listening on port 8000\n"

    def events(self, since: int | None = None) -> Iterator[dict[str, Any]]:
        return iter(())

    def close(self) -> None:
        pass
