"""Exception hierarchy for :mod:`feedback_manager`.

All exceptions raised by this package derive from :class:`FeedbackManagerError`
so callers can catch the whole domain with a single ``except`` clause while
still being able to discriminate on the specific failure mode when needed.
Every exception preserves the context (feedback id, stage, cause) that was
available when it was raised.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID


class FeedbackManagerError(Exception):
    """Base class for every error raised by feedback_manager."""

    def __init__(
        self, message: str, *, feedback_id: UUID | str | None = None, **context: Any
    ) -> None:
        super().__init__(message)
        self.feedback_id = feedback_id
        self.context = context

    def __str__(self) -> str:
        base = super().__str__()
        if self.feedback_id is not None:
            return f"{base} (feedback_id={self.feedback_id})"
        return base


class FeedbackValidationError(FeedbackManagerError):
    """Raised when a feedback event fails domain validation."""


class FeedbackNotFoundError(FeedbackManagerError):
    """Raised when a feedback event cannot be found by id."""


class FeedbackLifecycleError(FeedbackManagerError):
    """Raised when an illegal or unsafe lifecycle transition is attempted."""

    def __init__(
        self,
        message: str,
        *,
        feedback_id: UUID | str | None = None,
        current_status: str | None = None,
        requested_status: str | None = None,
        **context: Any,
    ) -> None:
        super().__init__(
            message,
            feedback_id=feedback_id,
            current_status=current_status,
            requested_status=requested_status,
            **context,
        )
        self.current_status = current_status
        self.requested_status = requested_status


class FeedbackRoutingError(FeedbackManagerError):
    """Raised when routing a feedback event to handlers fails."""


class FeedbackHandlerError(FeedbackManagerError):
    """Raised when a feedback handler fails to process an event."""


class FeedbackStoreError(FeedbackManagerError):
    """Raised when the underlying :class:`FeedbackStore` fails an operation."""


class FeedbackCorrelationError(FeedbackManagerError):
    """Raised when correlation/provenance resolution fails."""


class FeedbackSerializationError(FeedbackManagerError):
    """Raised when serializing or deserializing a feedback event fails."""


class FeedbackConfigurationError(FeedbackManagerError):
    """Raised when :class:`FeedbackManager` (or a dependency) is misconfigured."""


__all__ = [
    "FeedbackConfigurationError",
    "FeedbackCorrelationError",
    "FeedbackHandlerError",
    "FeedbackLifecycleError",
    "FeedbackManagerError",
    "FeedbackNotFoundError",
    "FeedbackRoutingError",
    "FeedbackSerializationError",
    "FeedbackStoreError",
    "FeedbackValidationError",
]
