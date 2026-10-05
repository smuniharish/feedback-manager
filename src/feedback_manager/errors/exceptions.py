"""Typed exceptions raised by feedback-manager.

Every failure the package surfaces derives from `FeedbackManagerError`, so one
``except FeedbackManagerError`` clause covers the whole package while each
subclass still identifies a single failure mode. When a failure wraps another
exception, the original exception is chained as ``__cause__``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID


class FeedbackManagerError(Exception):
    """Base class of every error raised by feedback-manager.

    Attributes:
        feedback_id: The feedback event the error concerns, when known.
        context: Additional structured details, such as the failed ``stage``.
    """

    def __init__(self, message: str, *, feedback_id: UUID | None = None, **context: Any) -> None:
        super().__init__(message)
        self.feedback_id = feedback_id
        self.context = context

    def __str__(self) -> str:
        message = super().__str__()
        if self.feedback_id is None:
            return message
        return f"{message} (feedback_id={self.feedback_id})"


class FeedbackValidationError(FeedbackManagerError, ValueError):
    """Raised when feedback input, a query, or a domain value is invalid."""


class FeedbackConfigurationError(FeedbackManagerError):
    """Raised when `FeedbackManager` or one of its collaborators is wired incorrectly."""


class FeedbackNotFoundError(FeedbackManagerError, LookupError):
    """Raised when no feedback event exists for a ``feedback_id``."""


class FeedbackLifecycleError(FeedbackManagerError):
    """Raised when a lifecycle transition is illegal or is denied by a lifecycle policy.

    Attributes:
        current_status: The status the event was in, when known.
        requested_status: The status the transition asked for, when known.
    """

    def __init__(
        self,
        message: str,
        *,
        feedback_id: UUID | None = None,
        current_status: str | None = None,
        requested_status: str | None = None,
        **context: Any,
    ) -> None:
        super().__init__(message, feedback_id=feedback_id, **context)
        self.current_status = current_status
        self.requested_status = requested_status


class FeedbackConflictError(FeedbackLifecycleError):
    """Raised when an event's status changed concurrently, so a transition could not apply.

    `FeedbackStore.transition` raises it when the stored status differs from
    the expected one. `FeedbackManager` re-reads the event and retries, so
    applications only see it when the event keeps changing under contention.
    """


class FeedbackStoreError(FeedbackManagerError):
    """Raised when the configured `FeedbackStore` fails an operation."""


class FeedbackCorrelationError(FeedbackManagerError):
    """Raised when correlation or provenance resolution fails in ``BLOCKING`` mode."""


class FeedbackRoutingError(FeedbackManagerError):
    """Raised when the router fails in ``BLOCKING`` mode."""


class FeedbackHandlerError(FeedbackManagerError):
    """Raised when a handler fails in ``BLOCKING`` mode."""


class FeedbackSubscriberError(FeedbackManagerError):
    """Raised when a subscriber fails in ``BLOCKING`` mode."""


__all__ = [
    "FeedbackConfigurationError",
    "FeedbackConflictError",
    "FeedbackCorrelationError",
    "FeedbackHandlerError",
    "FeedbackLifecycleError",
    "FeedbackManagerError",
    "FeedbackNotFoundError",
    "FeedbackRoutingError",
    "FeedbackStoreError",
    "FeedbackSubscriberError",
    "FeedbackValidationError",
]
