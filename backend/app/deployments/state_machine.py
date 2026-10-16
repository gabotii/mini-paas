"""Deployment state machine.

The only place that decides whether a status change is legal. Both the API (cancel) and the
workers (pipeline, crash watcher, reaper) call `transition()`; an illegal transition raises
`InvalidTransition` and nothing is persisted.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from app.models.enums import DeploymentStatus as S
from app.models.enums import FailureReason

TRANSITIONS: dict[S, frozenset[S]] = {
    S.QUEUED: frozenset({S.BUILDING, S.DEPLOYING, S.CANCELLED, S.FAILED}),
    S.BUILDING: frozenset({S.DEPLOYING, S.CANCELLED, S.FAILED}),
    S.DEPLOYING: frozenset({S.HEALTH_CHECKING, S.CANCELLED, S.FAILED}),
    S.HEALTH_CHECKING: frozenset({S.RUNNING, S.CANCELLED, S.FAILED}),
    S.RUNNING: frozenset({S.STOPPED, S.FAILED}),
    S.STOPPED: frozenset(),
    S.FAILED: frozenset(),
    S.CANCELLED: frozenset(),
}

# Failure reason implied by the stage a deployment was in when it failed.
STAGE_FAILURE: dict[S, FailureReason] = {
    S.QUEUED: FailureReason.INTERNAL_ERROR,
    S.BUILDING: FailureReason.BUILD_FAILED,
    S.DEPLOYING: FailureReason.DEPLOY_FAILED,
    S.HEALTH_CHECKING: FailureReason.HEALTH_CHECK_FAILED,
    S.RUNNING: FailureReason.RUNTIME_CRASHED,
}


class InvalidTransition(Exception):
    def __init__(self, current: S, target: S) -> None:
        super().__init__(f"Invalid deployment transition {current} -> {target}")
        self.current = current
        self.target = target


class DeploymentLike(Protocol):
    status: S
    started_at: datetime | None
    deployed_at: datetime | None
    finished_at: datetime | None
    stopped_at: datetime | None
    failure_reason: FailureReason | None
    error_message: str | None


def can_transition(current: S, target: S) -> bool:
    return target in TRANSITIONS[current]


def transition(
    deployment: DeploymentLike,
    target: S,
    *,
    reason: FailureReason | None = None,
    error: str | None = None,
    now: datetime | None = None,
) -> None:
    """Apply a status change and its timestamp side effects, or raise InvalidTransition."""
    current = deployment.status
    if not can_transition(current, target):
        raise InvalidTransition(current, target)
    now = now or datetime.now(UTC)

    if current == S.QUEUED and target in (S.BUILDING, S.DEPLOYING):
        deployment.started_at = now
    if target == S.RUNNING:
        deployment.deployed_at = now
        deployment.finished_at = now
    if target in (S.FAILED, S.CANCELLED) and deployment.finished_at is None:
        deployment.finished_at = now
    if target == S.STOPPED or (target == S.FAILED and current == S.RUNNING):
        deployment.stopped_at = now
    if target == S.FAILED:
        deployment.failure_reason = reason or STAGE_FAILURE.get(current, FailureReason.INTERNAL_ERROR)
        deployment.error_message = (error or "Deployment failed")[:4000]

    deployment.status = target


def is_active(status: S) -> bool:
    return status in S.active()
