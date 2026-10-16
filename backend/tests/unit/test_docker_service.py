"""DockerService against a fake low-level API and a fake docker CLI (no daemon needed)."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Any

import docker.errors
import pytest

from app.docker.service import (
    BuildFailed,
    BuildOptions,
    BuildTimeout,
    ContainerSpec,
    DockerService,
    DockerUnavailable,
)
from app.docker.stats import parse_docker_time, parse_stats, split_log_line


class FakeAPI:
    def __init__(self) -> None:
        self.created: dict[str, Any] = {}
        self.host_config: dict[str, Any] = {}
        self.calls: list[str] = []
        self.inspect: dict[str, dict[str, Any]] = {}

    def create_host_config(self, **kwargs: Any) -> dict[str, Any]:
        self.host_config = kwargs
        return kwargs

    def create_endpoint_config(self, **kwargs: Any) -> dict[str, Any]:
        return kwargs

    def create_networking_config(self, config: dict[str, Any]) -> dict[str, Any]:
        return {"EndpointsConfig": config}

    def create_container(self, **kwargs: Any) -> dict[str, str]:
        self.created = kwargs
        return {"Id": "abc123"}

    def remove_container(self, container: str, **kwargs: Any) -> None:
        self.calls.append(f"remove:{container}")
        raise docker.errors.NotFound("gone")

    def inspect_container(self, container: str) -> dict[str, Any]:
        if container not in self.inspect:
            raise docker.errors.NotFound("missing")
        return dict(self.inspect[container])

    def inspect_image(self, tag: str) -> dict[str, str]:
        return {"Id": "sha256:feed"}

    def start(self, container: str) -> None:
        raise docker.errors.APIError("boom", explanation="port is already allocated")


class FakeClient:
    def __init__(self) -> None:
        self.api = FakeAPI()

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass


SPEC = ContainerSpec(
    name="paas-project-1-deployment-2",
    image="mini-paas/project-1:deployment-2",
    network="paas-net-1",
    alias="d-abc",
    environment={"PORT": "8000"},
    labels={"paas.project_id": "1"},
    cpu_limit=0.5,
    memory_mb=256,
    pids_limit=128,
    read_only=True,
    tmpfs_mb=64,
)


def service() -> tuple[DockerService, FakeAPI]:
    client = FakeClient()
    return DockerService(client_factory=lambda: client), client.api


def test_container_is_created_with_hardened_profile() -> None:
    svc, api = service()
    assert svc.create_container(SPEC) == "abc123"
    hc = api.host_config
    assert hc["cap_drop"] == ["ALL"]
    assert hc["security_opt"] == ["no-new-privileges:true"]
    assert hc["privileged"] is False
    assert hc["read_only"] is True
    assert hc["mem_limit"] == "256m"
    assert hc["memswap_limit"] == "256m"
    assert hc["nano_cpus"] == 500_000_000
    assert hc["pids_limit"] == 128
    assert hc["network_mode"] == "paas-net-1"
    assert hc["publish_all_ports"] is False
    assert "noexec" in hc["tmpfs"]["/tmp"]
    assert hc["restart_policy"]["MaximumRetryCount"] == 3
    assert "binds" not in hc and "volumes" not in api.created  # never mounts host paths
    assert api.created["labels"]["paas.managed"] == "true"
    assert api.created["networking_config"]["EndpointsConfig"]["paas-net-1"]["aliases"] == ["d-abc"]


def test_api_errors_are_translated() -> None:
    svc, _ = service()
    from app.docker.service import DockerError

    with pytest.raises(DockerError, match="port is already allocated"):
        svc.start_container("abc123")


def test_remove_missing_container_is_idempotent() -> None:
    svc, api = service()
    svc.remove_container("nope")
    assert api.calls == ["remove:nope"]


def test_status_of_missing_container_is_none() -> None:
    svc, _ = service()
    assert svc.get_container_status("nope") is None


def test_status_parsing() -> None:
    svc, api = service()
    api.inspect["c1"] = {
        "Id": "c1",
        "Name": "/paas-x",
        "RestartCount": 2,
        "State": {
            "Status": "exited",
            "Running": False,
            "Restarting": False,
            "ExitCode": 137,
            "OOMKilled": True,
            "StartedAt": "2026-01-01T10:00:00.123456789Z",
            "FinishedAt": "0001-01-01T00:00:00Z",
        },
        "Config": {"Labels": {"paas.deployment_id": "d1"}},
    }
    state = svc.get_container_status("c1")
    assert state is not None
    assert state.name == "paas-x"
    assert state.exit_code == 137
    assert state.oom_killed
    assert state.restart_count == 2
    assert state.started_at is not None and state.started_at.microsecond == 123456
    assert state.finished_at is None
    assert state.labels == {"paas.deployment_id": "d1"}


def test_unreachable_daemon_raises_unavailable() -> None:
    def broken() -> Any:
        raise docker.errors.DockerException("Error while fetching server API version")

    svc = DockerService(client_factory=broken)
    with pytest.raises(DockerUnavailable):
        svc.get_container_status("x")
    assert svc.ping() is False


# ------------------------------------------------------------------ builds via a fake CLI
def fake_cli(tmp_path: Path, body: str) -> list[str]:
    script = tmp_path / "fake_docker.py"
    script.write_text(textwrap.dedent(body))
    return [sys.executable, str(script)]


def test_build_streams_output_and_returns_image_id(tmp_path: Path) -> None:
    cli = fake_cli(
        tmp_path,
        """
        import sys
        if sys.argv[1:3] == ["buildx", "build"]:
            print("#1 [internal] load build definition", flush=True)
            print("\\x1b[32m#2 DONE 0.1s\\x1b[0m", flush=True)
        sys.exit(0)
        """,
    )
    client = FakeClient()
    svc = DockerService(client_factory=lambda: client, docker_cli=cli)
    lines: list[str] = []
    image_id = svc.build_image(
        str(tmp_path), "Dockerfile", "img:1", {"paas.managed": "true"},
        BuildOptions(timeout_seconds=30, use_isolated_builder=False), on_line=lines.append,
    )  # fmt: skip
    assert image_id == "sha256:feed"
    assert lines == ["#1 [internal] load build definition", "#2 DONE 0.1s"]  # ANSI stripped


def test_build_failure_surfaces_error_line(tmp_path: Path) -> None:
    cli = fake_cli(
        tmp_path,
        """
        import sys
        print("#5 RUN pip install nope")
        print("ERROR: failed to solve: process did not complete successfully")
        sys.exit(1)
        """,
    )
    svc = DockerService(client_factory=FakeClient, docker_cli=cli)
    with pytest.raises(BuildFailed, match="failed to solve"):
        svc.build_image(
            str(tmp_path), "Dockerfile", "img:1", {}, BuildOptions(30, False), on_line=lambda _: None
        )


def test_build_timeout_kills_process(tmp_path: Path) -> None:
    cli = fake_cli(tmp_path, "import time\nprint('working', flush=True)\ntime.sleep(30)\n")
    svc = DockerService(client_factory=FakeClient, docker_cli=cli)
    with pytest.raises(BuildTimeout):
        svc.build_image(
            str(tmp_path), "Dockerfile", "img:1", {}, BuildOptions(1, False), on_line=lambda _: None
        )


# ------------------------------------------------------------------ pure helpers
def test_parse_stats_matches_docker_cli_formula() -> None:
    raw = {
        "cpu_stats": {"cpu_usage": {"total_usage": 2_000_000}, "system_cpu_usage": 20_000_000, "online_cpus": 2},
        "precpu_stats": {"cpu_usage": {"total_usage": 1_000_000}, "system_cpu_usage": 10_000_000},
        "memory_stats": {"usage": 300 * 1024 * 1024, "limit": 512 * 1024 * 1024,
                         "stats": {"inactive_file": 50 * 1024 * 1024}},
        "networks": {"eth0": {"rx_bytes": 1000, "tx_bytes": 500}, "eth1": {"rx_bytes": 24, "tx_bytes": 0}},
    }  # fmt: skip
    stats = parse_stats(raw)
    assert stats.cpu_percent == 20.0
    assert stats.memory_usage == 250 * 1024 * 1024
    assert stats.memory_limit == 512 * 1024 * 1024
    assert (stats.network_rx, stats.network_tx) == (1024, 500)


def test_parse_stats_handles_missing_fields() -> None:
    stats = parse_stats({})
    assert stats.cpu_percent == 0.0
    assert stats.memory_usage == 0


def test_docker_time_and_log_lines() -> None:
    parsed = parse_docker_time("2026-10-05T12:30:01.123456789+00:00")
    assert parsed is not None and parsed.second == 1
    ts, message = split_log_line("2026-10-05T12:30:01.000000000Z Listening on port 8000")
    assert ts is not None and message == "Listening on port 8000"
    ts, message = split_log_line("no timestamp here")
    assert ts is None and message == "no timestamp here"
