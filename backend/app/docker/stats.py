"""Pure helpers that turn raw Docker API payloads into platform values (unit-testable)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class ContainerStats:
    cpu_percent: float
    memory_usage: int
    memory_limit: int
    network_rx: int
    network_tx: int


def _get(d: dict[str, Any], *path: str, default: Any = 0) -> Any:
    for key in path:
        if not isinstance(d, dict) or key not in d:
            return default
        d = d[key]
    return d


def parse_stats(raw: dict[str, Any]) -> ContainerStats:
    cpu_total = _get(raw, "cpu_stats", "cpu_usage", "total_usage")
    pre_total = _get(raw, "precpu_stats", "cpu_usage", "total_usage")
    system = _get(raw, "cpu_stats", "system_cpu_usage")
    pre_system = _get(raw, "precpu_stats", "system_cpu_usage")
    online = (
        _get(raw, "cpu_stats", "online_cpus")
        or len(_get(raw, "cpu_stats", "cpu_usage", "percpu_usage", default=[]) or [])
        or 1
    )
    cpu_delta = cpu_total - pre_total
    system_delta = system - pre_system
    cpu_percent = (cpu_delta / system_delta) * online * 100.0 if system_delta > 0 and cpu_delta > 0 else 0.0

    usage = int(_get(raw, "memory_stats", "usage"))
    mem_stats = _get(raw, "memory_stats", "stats", default={}) or {}
    # cgroup v2 reports inactive_file, v1 reports cache; `docker stats` subtracts the same.
    cache = int(mem_stats.get("inactive_file", mem_stats.get("total_inactive_file", 0)) or 0)
    memory = max(usage - cache, 0)
    limit = int(_get(raw, "memory_stats", "limit"))

    rx = tx = 0
    for iface in (raw.get("networks") or {}).values():
        rx += int(iface.get("rx_bytes", 0))
        tx += int(iface.get("tx_bytes", 0))

    return ContainerStats(
        cpu_percent=round(cpu_percent, 2),
        memory_usage=memory,
        memory_limit=limit,
        network_rx=rx,
        network_tx=tx,
    )


def parse_docker_time(value: str | None) -> datetime | None:
    """Parse Docker's RFC3339Nano timestamps (nanoseconds are truncated to microseconds)."""
    if not value or value.startswith("0001-01-01"):
        return None
    value = value.rstrip("Z")
    if "." in value:
        head, frac = value.split(".", 1)
        tz = ""
        for sep in ("+", "-"):
            if sep in frac:
                frac, tz = frac.split(sep, 1)
                tz = sep + tz
                break
        value = f"{head}.{frac[:6]}{tz}"
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def split_log_line(line: str) -> tuple[datetime | None, str]:
    """Split a `docker logs --timestamps` line into (timestamp, message)."""
    stamp, _, message = line.partition(" ")
    try:
        return parse_docker_time(stamp), message
    except ValueError:
        return None, line
