"""HTTP health checks for freshly started containers.

The worker joins the project network and probes `http://<alias>:<port><path>`
repeatedly until it gets a 2xx/3xx response, the container exits, or the timeout elapses.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

import httpx

from app.docker.service import ContainerState


@dataclass
class HealthResult:
    ok: bool
    url: str
    attempts: int
    duration_ms: int
    status_code: int | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


Probe = Callable[[str, float], int]


def http_probe(url: str, timeout: float) -> int:
    response = httpx.get(url, timeout=timeout, follow_redirects=False)
    return response.status_code


def run_health_check(
    url: str,
    *,
    container_state: Callable[[], ContainerState | None],
    timeout_seconds: float,
    interval_seconds: float,
    probe: Probe = http_probe,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    on_attempt: Callable[[int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> HealthResult:
    started = clock()
    attempts = 0
    status: int | None = None
    error: str | None = None

    while True:
        if should_cancel is not None and should_cancel():
            return HealthResult(False, url, attempts, int((clock() - started) * 1000), error="cancelled")
        state = container_state()
        if state is None:
            error = "Container disappeared"
            break
        if not state.running and not state.restarting:
            error = f"Container exited with code {state.exit_code}" + (
                " (out of memory)" if state.oom_killed else ""
            )
            break

        attempts += 1
        try:
            status = probe(url, min(5.0, max(timeout_seconds, 1.0)))
            if 200 <= status < 400:
                return HealthResult(True, url, attempts, int((clock() - started) * 1000), status_code=status)
            error = f"HTTP {status}"
        except httpx.HTTPError as exc:
            error = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        if on_attempt:
            on_attempt(attempts, error)

        if clock() - started + interval_seconds > timeout_seconds:
            error = f"No healthy response within {timeout_seconds:.0f}s (last: {error})"
            break
        sleep(interval_seconds)

    return HealthResult(
        False, url, attempts, int((clock() - started) * 1000), status_code=status, error=error
    )
