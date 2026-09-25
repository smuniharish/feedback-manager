"""Unit tests for the core :class:`FeedbackEvent` domain model."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from feedback_manager.core import (
    FeedbackCategory,
    FeedbackEvent,
    FeedbackSource,
    FeedbackStatus,
    FeedbackTarget,
    FeedbackTargetType,
)


def _target() -> FeedbackTarget:
    return FeedbackTarget(type=FeedbackTargetType.GENERATION, id="gen-1")


def test_minimal_event_has_sane_defaults() -> None:
    event = FeedbackEvent(
        source=FeedbackSource.HUMAN,
        category=FeedbackCategory.CORRECTION,
        target=_target(),
    )
    assert event.status == FeedbackStatus.CREATED
    assert event.payload == {}
    assert event.metadata == {}
    assert event.correlation is None
    assert event.provenance is None
    assert event.feedback_id is not None


def test_event_is_frozen() -> None:
    event = FeedbackEvent(
        source=FeedbackSource.HUMAN, category=FeedbackCategory.CORRECTION, target=_target()
    )
    with pytest.raises(ValidationError):
        event.status = FeedbackStatus.RECEIVED  # type: ignore[misc]


def test_with_status_returns_new_instance() -> None:
    event = FeedbackEvent(
        source=FeedbackSource.HUMAN, category=FeedbackCategory.CORRECTION, target=_target()
    )
    updated = event.with_status(FeedbackStatus.RECEIVED)
    assert updated is not event
    assert event.status == FeedbackStatus.CREATED
    assert updated.status == FeedbackStatus.RECEIVED
    assert updated.updated_at >= event.updated_at


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValidationError):
        FeedbackEvent(
            source=FeedbackSource.HUMAN,
            category=FeedbackCategory.CORRECTION,
            target=_target(),
            created_at=datetime.now(),
        )


def test_roundtrip_serialization() -> None:
    event = FeedbackEvent(
        source=FeedbackSource.TOOL,
        category=FeedbackCategory.TIMEOUT,
        target=FeedbackTarget(type=FeedbackTargetType.TOOL_CALL, id="call-1"),
        payload={"detail": "slow"},
    )
    dumped = event.model_dump_json()
    restored = FeedbackEvent.model_validate_json(dumped)
    assert restored == event


def test_open_source_accepts_custom_values() -> None:
    """Sources are an open value type: applications may define new ones."""
    event = FeedbackEvent(
        source=FeedbackSource("mcp_server"),
        category=FeedbackCategory.COMMENT,
        target=_target(),
    )
    assert event.source == "mcp_server"


def test_timestamps_default_to_utc_now() -> None:
    before = datetime.now(UTC)
    event = FeedbackEvent(
        source=FeedbackSource.SYSTEM, category=FeedbackCategory.FAILURE, target=_target()
    )
    after = datetime.now(UTC)
    assert before <= event.created_at <= after
