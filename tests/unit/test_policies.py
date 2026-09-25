"""Unit tests for failure isolation and retention policies."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from feedback_manager.core import (
    FeedbackCategory,
    FeedbackEvent,
    FeedbackSource,
    FeedbackStatus,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.policies.failure import FailureMode, FailurePolicy, FeedbackStage
from feedback_manager.policies.retention import RetentionPolicy


class _Boom(Exception):
    pass


async def _raise() -> None:
    raise _Boom("nope")


async def test_best_effort_stage_swallows_and_returns_none() -> None:
    policy = FailurePolicy()
    result = await policy.run_stage(FeedbackStage.HANDLER, _raise)
    assert result is None


async def test_blocking_stage_reraises() -> None:
    policy = FailurePolicy()
    with pytest.raises(_Boom):
        await policy.run_stage(FeedbackStage.STORE, _raise)


async def test_on_error_callback_invoked_for_best_effort() -> None:
    policy = FailurePolicy()
    captured: list[tuple[FeedbackStage, BaseException]] = []
    await policy.run_stage(
        FeedbackStage.SUBSCRIBER, _raise, on_error=lambda stage, exc: captured.append((stage, exc))
    )
    assert len(captured) == 1
    assert captured[0][0] == FeedbackStage.SUBSCRIBER
    assert isinstance(captured[0][1], _Boom)


async def test_on_error_callback_invoked_before_reraise_for_blocking() -> None:
    policy = FailurePolicy()
    captured: list[BaseException] = []
    with pytest.raises(_Boom):
        await policy.run_stage(
            FeedbackStage.SERIALIZATION, _raise, on_error=lambda stage, exc: captured.append(exc)
        )
    assert len(captured) == 1


def test_mode_override_per_stage() -> None:
    policy = FailurePolicy(modes={FeedbackStage.HANDLER: FailureMode.BLOCKING})
    assert policy.mode_for(FeedbackStage.HANDLER) is FailureMode.BLOCKING
    assert policy.mode_for(FeedbackStage.ROUTING) is FailureMode.BEST_EFFORT


def _event(created_at: datetime) -> FeedbackEvent:
    return FeedbackEvent(
        source=FeedbackSource.HUMAN,
        category=FeedbackCategory.CORRECTION,
        target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id="gen-1"),
        created_at=created_at,
    )


def test_retention_policy_expires_old_events() -> None:
    policy = RetentionPolicy(max_pending_age=timedelta(days=1))
    old_event = _event(datetime.now(UTC) - timedelta(days=2))
    fresh_event = _event(datetime.now(UTC))
    assert policy.is_expired(old_event) is True
    assert policy.is_expired(fresh_event) is False


def test_retention_policy_ignores_terminal_events() -> None:
    policy = RetentionPolicy(max_pending_age=timedelta(days=1))
    old_event = _event(datetime.now(UTC) - timedelta(days=2)).with_status(FeedbackStatus.RESOLVED)
    assert policy.is_expired(old_event) is False


def test_retention_policy_without_max_age_never_expires() -> None:
    policy = RetentionPolicy()
    old_event = _event(datetime.now(UTC) - timedelta(days=3650))
    assert policy.is_expired(old_event) is False
