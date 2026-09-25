"""Unit tests for the feedback error hierarchy."""

from __future__ import annotations

from uuid import uuid4

import pytest

from feedback_manager.errors import (
    FeedbackConfigurationError,
    FeedbackCorrelationError,
    FeedbackHandlerError,
    FeedbackLifecycleError,
    FeedbackManagerError,
    FeedbackNotFoundError,
    FeedbackRoutingError,
    FeedbackSerializationError,
    FeedbackStoreError,
    FeedbackValidationError,
)

ALL_ERRORS = [
    FeedbackValidationError,
    FeedbackNotFoundError,
    FeedbackLifecycleError,
    FeedbackRoutingError,
    FeedbackHandlerError,
    FeedbackStoreError,
    FeedbackCorrelationError,
    FeedbackSerializationError,
    FeedbackConfigurationError,
]


@pytest.mark.parametrize("error_cls", ALL_ERRORS)
def test_every_error_derives_from_base(error_cls: type[FeedbackManagerError]) -> None:
    assert issubclass(error_cls, FeedbackManagerError)


def test_feedback_id_is_included_in_message() -> None:
    feedback_id = uuid4()
    error = FeedbackNotFoundError("not found", feedback_id=feedback_id)
    assert str(feedback_id) in str(error)


def test_error_without_feedback_id_omits_suffix() -> None:
    error = FeedbackStoreError("boom")
    assert str(error) == "boom"


def test_extra_context_is_preserved() -> None:
    error = FeedbackStoreError("boom", feedback_id=uuid4(), stage="store")
    assert error.context["stage"] == "store"


def test_lifecycle_error_preserves_statuses() -> None:
    error = FeedbackLifecycleError(
        "illegal", feedback_id=uuid4(), current_status="resolved", requested_status="received"
    )
    assert error.current_status == "resolved"
    assert error.requested_status == "received"
