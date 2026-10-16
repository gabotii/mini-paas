"""Health checks, proxy configuration, port selection and runtime log helpers."""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.agent.log_follower import RateGate, guess_level
from app.core.config import Settings
from app.deployments.health import run_health_check
from app.deployments.naming import container_name, network_alias
from app.docker.service import ContainerState
from app.models import LogLevel, Runtime
from app.routing.domains import DomainService
from app.routing.traefik import Route, render_config
from app.workers.pipeline import effective_port


def state(
    running: bool = True, restarting: bool = False, exit_code: int | None = None, oom: bool = False
) -> ContainerState:
    return ContainerState(
        id="c", name="c", status="running" if running else "exited", running=running, restarting=restarting,
        exit_code=exit_code, oom_killed=oom, started_at=None, finished_at=None, restart_count=0,
    )  # fmt: skip


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_health_check_passes_after_retries() -> None:
    responses = iter([httpx.ConnectError("refused"), 503, 200])

    def probe(url: str, timeout: float) -> int:
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        assert isinstance(value, int)
        return value

    clock = Clock()
    result = run_health_check(
        "http://d-1:8000/", container_state=lambda: state(), timeout_seconds=30, interval_seconds=2,
        probe=probe, sleep=clock.sleep, clock=clock,
    )  # fmt: skip
    assert result.ok
    assert result.attempts == 3
    assert result.status_code == 200


def test_health_check_times_out() -> None:
    clock = Clock()
    result = run_health_check(
        "http://d-1:8000/", container_state=lambda: state(), timeout_seconds=10, interval_seconds=2,
        probe=lambda u, t: 500, sleep=clock.sleep, clock=clock,
    )  # fmt: skip
    assert not result.ok
    assert result.status_code == 500
    assert "within 10s" in (result.error or "")


def test_health_check_fails_fast_when_container_exits() -> None:
    clock = Clock()
    result = run_health_check(
        "http://d-1:8000/", container_state=lambda: state(running=False, exit_code=137, oom=True),
        timeout_seconds=60, interval_seconds=2, probe=lambda u, t: 200, sleep=clock.sleep, clock=clock,
    )  # fmt: skip
    assert not result.ok
    assert result.attempts == 0
    assert result.error == "Container exited with code 137 (out of memory)"


def test_health_check_keeps_waiting_while_restarting() -> None:
    states = iter([state(running=False, restarting=True), state()])
    clock = Clock()
    result = run_health_check(
        "u", container_state=lambda: next(states), timeout_seconds=60, interval_seconds=1,
        probe=lambda u, t: 204, sleep=clock.sleep, clock=clock,
    )  # fmt: skip
    assert result.ok


def test_health_check_can_be_cancelled() -> None:
    result = run_health_check(
        "u", container_state=lambda: state(), timeout_seconds=60, interval_seconds=1,
        probe=lambda u, t: 500, should_cancel=lambda: True,
    )  # fmt: skip
    assert result.error == "cancelled"


@pytest.mark.parametrize(
    ("runtime", "env", "project_port", "default", "expected"),
    [
        (Runtime.PYTHON, {}, None, 8000, 8000),
        (Runtime.PYTHON, {"PORT": "9000"}, 7000, 8000, 9000),
        (Runtime.NODE, {}, 4000, 3000, 4000),
        (Runtime.NODE, {"PORT": "not-a-port"}, None, 3000, 3000),
        (Runtime.STATIC, {"PORT": "9000"}, 1234, 8080, 8080),
    ],
)
def test_effective_port(
    runtime: Runtime, env: dict[str, str], project_port: int | None, default: int, expected: int
) -> None:
    assert effective_port(runtime, env, project_port, default) == expected


def test_traefik_config_routes_running_projects() -> None:
    deployment_id = uuid.uuid4()
    config = render_config([Route("my-api", deployment_id, 8000)], DomainService(Settings()))
    router = config["http"]["routers"]["app-my-api"]
    assert router["rule"] == "Host(`my-api.localhost`)"
    assert router["entryPoints"] == ["web"]
    server = config["http"]["services"]["app-my-api"]["loadBalancer"]["servers"][0]["url"]
    assert server == f"http://{network_alias(deployment_id)}:8000"
    assert render_config([], DomainService(Settings())) == {}


def test_domain_service_urls() -> None:
    domains = DomainService(Settings(apps_base_domain="apps.example.com", public_scheme="https"))
    assert domains.app_url("blog") == "https://blog.apps.example.com"
    assert DomainService(Settings(public_port=8080)).app_url("x1y") == "http://x1y.localhost:8080"


def test_resource_names_are_deterministic_and_dns_safe() -> None:
    project_id, deployment_id = uuid.uuid4(), uuid.uuid4()
    assert (
        container_name(project_id, deployment_id) == f"paas-project-{project_id}-deployment-{deployment_id}"
    )
    alias = network_alias(deployment_id)
    assert alias == network_alias(deployment_id)
    assert len(alias) <= 63


def test_log_level_guessing() -> None:
    assert guess_level("Traceback (most recent call last):") == LogLevel.ERROR
    assert guess_level("WARNING: deprecated") == LogLevel.WARNING
    assert guess_level("Listening on 8000") == LogLevel.INFO


def test_rate_gate_drops_and_reports() -> None:
    gate = RateGate(per_second=2)
    results = [gate.allow(now=100.0) for _ in range(5)]
    assert [allowed for allowed, _ in results] == [True, True, False, False, False]
    allowed, reported = gate.allow(now=101.0)
    assert allowed
    assert reported == 3
