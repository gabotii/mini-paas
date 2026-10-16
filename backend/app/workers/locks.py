"""Distributed per-project lock (Redis) serialising container lifecycle operations.

Deploy switch-over, stop, restart and delete of the same project never interleave, even when
they run on different worker processes.

Implemented with `SET NX EX` + an optimistic `WATCH/MULTI` compare-and-delete instead of Lua
scripts, so it behaves identically on Redis and on Redis-compatible servers with partial Lua
support. A lock that outlives its timeout simply expires (the holder crashed).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import redis


class ProjectBusy(Exception):
    pass


def _release(client: redis.Redis, key: str, token: str) -> None:
    with client.pipeline(transaction=True) as pipe:
        try:
            pipe.watch(key)  # type: ignore[no-untyped-call]
            if pipe.get(key) != token:
                pipe.unwatch()
                return  # expired and possibly taken by someone else: not ours to delete
            pipe.multi()
            pipe.delete(key)
            pipe.execute()
        except redis.WatchError:
            pass  # changed under us, so it is no longer ours


@contextmanager
def project_lock(
    client: redis.Redis,
    project_id: uuid.UUID,
    *,
    timeout: int = 300,
    wait: float = 120,
    poll: float = 0.2,
) -> Iterator[None]:
    key = f"lock:project:{project_id}"
    token = uuid.uuid4().hex
    deadline = time.monotonic() + wait
    while not client.set(key, token, nx=True, ex=timeout):
        if time.monotonic() >= deadline:
            raise ProjectBusy(f"Project {project_id} is busy with another operation")
        time.sleep(poll)
    try:
        yield
    finally:
        _release(client, key, token)
