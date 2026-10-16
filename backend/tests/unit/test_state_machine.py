from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from app.deployments.state_machine import InvalidTransition, can_transition, transition
from app.models import DeploymentStatus as S
from app.models import FailureReason


@dataclass
class FakeDeployment:
    status: S = S.QUEUED
    started_at: datetime | None = None
    deployed_at: datetime | None = None
    finished_at: datetime | None = None
    stopped_at: datetime | None = None
    failure_reason: FailureReason | None = None
    error_message: str | None = None


NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_happy_path_sets_timestamps() -> None:
    d = FakeDeployment()
    for target in (S.BUILDING, S.DEPLOYING, S.HEALTH_CHECKING, S.RUNNING):
        transition(d, target, now=NOW)
    assert d.status == S.RUNNING
    assert d.started_at == NOW
    assert d.deployed_at == NOW
    assert d.finished_at == NOW


def test_rollback_skips_build() -> None:
    d = FakeDeployment()
    transition(d, S.DEPLOYING, now=NOW)
    assert d.started_at == NOW


@pytest.mark.parametrize(
    ("stage", "reason"),
    [
        (S.BUILDING, FailureReason.BUILD_FAILED),
        (S.DEPLOYING, FailureReason.DEPLOY_FAILED),
        (S.HEALTH_CHECKING, FailureReason.HEALTH_CHECK_FAILED),
        (S.RUNNING, FailureReason.RUNTIME_CRASHED),
    ],
)
def test_failure_reason_derived_from_stage(stage: S, reason: FailureReason) -> None:
    d = FakeDeployment(status=stage)
    transition(d, S.FAILED, error="boom", now=NOW)
    assert d.failure_reason == reason
    assert d.error_message == "boom"
    assert d.finished_at == NOW


def test_explicit_reason_wins() -> None:
    d = FakeDeployment(status=S.BUILDING)
    transition(d, S.FAILED, reason=FailureReason.TIMEOUT)
    assert d.failure_reason == FailureReason.TIMEOUT


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.QUEUED, S.RUNNING),
        (S.BUILDING, S.HEALTH_CHECKING),
        (S.RUNNING, S.BUILDING),
        (S.STOPPED, S.RUNNING),
        (S.FAILED, S.QUEUED),
        (S.CANCELLED, S.BUILDING),
        (S.RUNNING, S.CANCELLED),
    ],
)
def test_invalid_transitions_raise_and_do_not_mutate(current: S, target: S) -> None:
    d = FakeDeployment(status=current)
    with pytest.raises(InvalidTransition):
        transition(d, target)
    assert d.status == current
    assert not can_transition(current, target)


def test_terminal_states_have_no_exits() -> None:
    for terminal in S.terminal():
        assert not any(can_transition(terminal, t) for t in S)


def test_error_message_is_truncated() -> None:
    d = FakeDeployment(status=S.BUILDING)
    transition(d, S.FAILED, error="x" * 10_000)
    assert d.error_message is not None
    assert len(d.error_message) == 4000
