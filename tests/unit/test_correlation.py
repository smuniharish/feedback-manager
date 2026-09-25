"""Unit tests for the default correlator."""

from __future__ import annotations

from feedback_manager.core import (
    ExecutionContext,
    FeedbackCategory,
    FeedbackEvent,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.correlation.correlator import DefaultFeedbackCorrelator


def _event() -> FeedbackEvent:
    return FeedbackEvent(
        source=FeedbackSource.HUMAN,
        category=FeedbackCategory.CORRECTION,
        target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id="gen-1"),
    )


async def test_correlation_uses_run_id_when_available() -> None:
    correlator = DefaultFeedbackCorrelator()
    context = ExecutionContext(run_id="run-123", thread_id="thread-456")
    correlation = await correlator.correlate(_event(), context)
    assert correlation.correlation_id == "run-123"
    assert correlation.execution is context


async def test_correlation_falls_back_to_thread_id() -> None:
    correlator = DefaultFeedbackCorrelator()
    context = ExecutionContext(thread_id="thread-456")
    correlation = await correlator.correlate(_event(), context)
    assert correlation.correlation_id == "thread-456"


async def test_correlation_generates_random_id_without_context() -> None:
    correlator = DefaultFeedbackCorrelator()
    first = await correlator.correlate(_event(), None)
    second = await correlator.correlate(_event(), None)
    assert first.correlation_id != second.correlation_id
