"""Failure isolation and retention policies."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from feedback_manager import (
    FeedbackEvent,
    FeedbackStatus,
    FeedbackTarget,
    FeedbackValidationError,
)
from feedback_manager.errors import (
    FeedbackConfigurationError,
    FeedbackCorrelationError,
    FeedbackHandlerError,
    FeedbackManagerError,
    FeedbackRoutingError,
    FeedbackSubscriberError,
)
from feedback_manager.policies import FailureMode, FailurePolicy, FeedbackStage, RetentionPolicy
from tests.strategies import NAIVE


class BoomError(Exception):
    pass


async def _fail() -> None:
    raise BoomError("exploded")


async def _succeed() -> str:
    return "ok"


class TestFailurePolicy:
    def test_every_stage_defaults_to_best_effort(self) -> None:
        policy = FailurePolicy()

        assert dict(policy.modes) == dict.fromkeys(FeedbackStage, FailureMode.BEST_EFFORT)

    def test_overrides_merge_with_defaults_and_accept_strings(self) -> None:
        policy = FailurePolicy(modes={"handler": "blocking"})  # type: ignore[dict-item]

        assert policy.mode_for(FeedbackStage.HANDLER) is FailureMode.BLOCKING
        assert policy.mode_for(FeedbackStage.ROUTING) is FailureMode.BEST_EFFORT
        with pytest.raises(TypeError):
            policy.modes[FeedbackStage.ROUTING] = FailureMode.BLOCKING  # type: ignore[index]

    @pytest.mark.parametrize("modes", [{"store": "blocking"}, {FeedbackStage.HANDLER: "fatal"}])
    def test_invalid_entries_are_configuration_errors(self, modes: dict[object, object]) -> None:
        with pytest.raises(FeedbackConfigurationError, match="invalid failure policy entry"):
            FailurePolicy(modes=modes)  # type: ignore[arg-type]

    async def test_success_returns_the_result(self) -> None:
        assert await FailurePolicy().run_stage(FeedbackStage.HANDLER, _succeed) == "ok"

    async def test_best_effort_logs_with_traceback_and_returns_none(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        seen: list[tuple[FeedbackStage, Exception]] = []
        feedback_id = uuid4()

        with caplog.at_level(logging.WARNING, logger="feedback_manager.policies.failure"):
            result = await FailurePolicy().run_stage(
                FeedbackStage.SUBSCRIBER,
                _fail,
                feedback_id=feedback_id,
                on_error=lambda stage, exc: seen.append((stage, exc)),
            )

        assert result is None
        assert [(stage, type(exc)) for stage, exc in seen] == [
            (FeedbackStage.SUBSCRIBER, BoomError)
        ]
        (record,) = caplog.records
        assert "stage='subscriber'" in record.getMessage()
        assert str(feedback_id) in record.getMessage()
        assert record.exc_info is not None
        assert record.exc_info[0] is BoomError

    async def test_best_effort_without_feedback_id(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="feedback_manager.policies.failure"):
            await FailurePolicy().run_stage(FeedbackStage.ROUTING, _fail)

        assert "feedback_id=None" in caplog.text

    @pytest.mark.parametrize(
        ("stage", "error_type"),
        [
            (FeedbackStage.CORRELATION, FeedbackCorrelationError),
            (FeedbackStage.PROVENANCE, FeedbackCorrelationError),
            (FeedbackStage.ROUTING, FeedbackRoutingError),
            (FeedbackStage.HANDLER, FeedbackHandlerError),
            (FeedbackStage.SUBSCRIBER, FeedbackSubscriberError),
        ],
    )
    async def test_blocking_wraps_the_failure_in_the_stage_error(
        self, stage: FeedbackStage, error_type: type[FeedbackManagerError]
    ) -> None:
        seen: list[Exception] = []
        policy = FailurePolicy(modes={stage: FailureMode.BLOCKING})

        with pytest.raises(error_type) as raised:
            await policy.run_stage(stage, _fail, on_error=lambda _, exc: seen.append(exc))

        assert isinstance(raised.value.__cause__, BoomError)
        assert raised.value.context == {"stage": stage.value}
        assert len(seen) == 1


def _event(status: FeedbackStatus, created_at: datetime) -> FeedbackEvent:
    return FeedbackEvent(
        source="human",
        category="comment",
        target=FeedbackTarget(type="generation", id="gen-1"),
        status=status,
        created_at=created_at,
    )


class TestRetentionPolicy:
    NOW = datetime(2026, 10, 1, tzinfo=UTC)

    @pytest.mark.parametrize("age", [timedelta(0), timedelta(seconds=-1)])
    def test_max_pending_age_must_be_positive(self, age: timedelta) -> None:
        with pytest.raises(FeedbackConfigurationError):
            RetentionPolicy(max_pending_age=age)

    def test_cutoff(self) -> None:
        policy = RetentionPolicy(max_pending_age=timedelta(days=1))

        assert policy.cutoff(self.NOW) == self.NOW - timedelta(days=1)
        assert policy.cutoff() <= datetime.now(UTC) - timedelta(days=1)
        with pytest.raises(FeedbackValidationError, match="timezone-aware"):
            policy.cutoff(NAIVE)

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (FeedbackStatus.CREATED, True),
            (FeedbackStatus.RECEIVED, True),
            (FeedbackStatus.ACKNOWLEDGED, True),
            (FeedbackStatus.HANDLED, False),
            (FeedbackStatus.RESOLVED, False),
            (FeedbackStatus.EXPIRED, False),
        ],
    )
    def test_only_expirable_statuses_expire(self, status: FeedbackStatus, expected: bool) -> None:
        policy = RetentionPolicy(max_pending_age=timedelta(days=1))
        old = _event(status, self.NOW - timedelta(days=2))

        assert policy.is_expired(old, now=self.NOW) is expected

    def test_recent_events_do_not_expire(self) -> None:
        policy = RetentionPolicy(max_pending_age=timedelta(days=1))
        fresh = _event(FeedbackStatus.RECEIVED, self.NOW - timedelta(hours=23))
        boundary = _event(FeedbackStatus.RECEIVED, self.NOW - timedelta(days=1))

        assert policy.is_expired(fresh, now=self.NOW) is False
        assert policy.is_expired(boundary, now=self.NOW) is False
