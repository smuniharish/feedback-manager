"""Unit tests for the bundled audit handler and the default serializer."""

from __future__ import annotations

from feedback_manager.contracts.handler import FeedbackContext
from feedback_manager.contracts.serializer import DefaultFeedbackSerializer
from feedback_manager.core import (
    FeedbackCategory,
    FeedbackEvent,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.handlers.audit import AuditFeedbackHandler


def _event() -> FeedbackEvent:
    return FeedbackEvent(
        source=FeedbackSource.HUMAN,
        category=FeedbackCategory.CORRECTION,
        target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id="gen-1"),
    )


async def test_audit_handler_logs_and_reports_handled(caplog) -> None:  # type: ignore[no-untyped-def]
    handler = AuditFeedbackHandler()
    with caplog.at_level("INFO", logger="feedback_manager.audit"):
        result = await handler.handle(_event(), FeedbackContext())
    assert result.handled is True
    assert "feedback audited" in caplog.text


def test_default_serializer_roundtrip() -> None:
    serializer = DefaultFeedbackSerializer()
    event = _event()
    data = serializer.serialize(event)
    restored = serializer.deserialize(data)
    assert restored == event
