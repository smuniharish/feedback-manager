"""What kind of feedback an event carries."""

from __future__ import annotations

from typing import ClassVar

from feedback_manager.core._types import OpenStringValue


class FeedbackCategory(OpenStringValue):
    """The kind of feedback a `FeedbackEvent` carries, independent of its source.

    A ``TIMEOUT`` can come from a tool, a person, or the system alike. The
    constants are the well-known categories; any other non-empty string is a
    valid category too, for example ``FeedbackCategory("policy_violation")``.
    """

    __slots__ = ()

    APPROVAL: ClassVar[FeedbackCategory]
    """Approval of an action or output."""
    REJECTION: ClassVar[FeedbackCategory]
    """Rejection of an action or output."""
    CORRECTION: ClassVar[FeedbackCategory]
    """A corrected version of an output."""
    RATING: ClassVar[FeedbackCategory]
    """A score or rating."""
    COMMENT: ClassVar[FeedbackCategory]
    """A free-form remark."""
    INTERRUPTION: ClassVar[FeedbackCategory]
    """Work that was stopped before it finished."""
    CANCELLATION: ClassVar[FeedbackCategory]
    """Work that was cancelled."""
    FAILURE: ClassVar[FeedbackCategory]
    """An error."""
    TIMEOUT: ClassVar[FeedbackCategory]
    """Work that exceeded its time limit."""
    VALIDATION: ClassVar[FeedbackCategory]
    """The result of a validation check."""
    QUALITY: ClassVar[FeedbackCategory]
    """An assessment of quality, typically from an evaluator."""
    UNCERTAINTY: ClassVar[FeedbackCategory]
    """Low confidence in an output."""
    REQUEST_FOR_HUMAN: ClassVar[FeedbackCategory]
    """A request for a person's input, such as a human-in-the-loop approval."""
    PARTIAL_RESULT: ClassVar[FeedbackCategory]
    """An incomplete output."""
    COMPLETION: ClassVar[FeedbackCategory]
    """Work that finished."""


FeedbackCategory.APPROVAL = FeedbackCategory("approval")
FeedbackCategory.REJECTION = FeedbackCategory("rejection")
FeedbackCategory.CORRECTION = FeedbackCategory("correction")
FeedbackCategory.RATING = FeedbackCategory("rating")
FeedbackCategory.COMMENT = FeedbackCategory("comment")
FeedbackCategory.INTERRUPTION = FeedbackCategory("interruption")
FeedbackCategory.CANCELLATION = FeedbackCategory("cancellation")
FeedbackCategory.FAILURE = FeedbackCategory("failure")
FeedbackCategory.TIMEOUT = FeedbackCategory("timeout")
FeedbackCategory.VALIDATION = FeedbackCategory("validation")
FeedbackCategory.QUALITY = FeedbackCategory("quality")
FeedbackCategory.UNCERTAINTY = FeedbackCategory("uncertainty")
FeedbackCategory.REQUEST_FOR_HUMAN = FeedbackCategory("request_for_human")
FeedbackCategory.PARTIAL_RESULT = FeedbackCategory("partial_result")
FeedbackCategory.COMPLETION = FeedbackCategory("completion")

__all__ = ["FeedbackCategory"]
