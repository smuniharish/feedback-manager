"""Open, extensible feedback category.

``FeedbackCategory`` is independent of :class:`~feedback_manager.core.sources.FeedbackSource`:
a category such as ``FAILURE`` or ``TIMEOUT`` can originate from a human, a
tool, an evaluator, or the system itself. Like ``FeedbackSource`` this is a
``str`` subclass with well-known constants, not a closed enum, so consumers
can register domain-specific categories without subclassing.
"""

from __future__ import annotations

from typing import ClassVar

from feedback_manager.core._open_value import OpenStringValue


class FeedbackCategory(OpenStringValue):
    """Categorizes *what kind* of feedback a :class:`FeedbackEvent` represents."""

    __slots__ = ()

    APPROVAL: ClassVar[FeedbackCategory]
    REJECTION: ClassVar[FeedbackCategory]
    CORRECTION: ClassVar[FeedbackCategory]
    RATING: ClassVar[FeedbackCategory]
    COMMENT: ClassVar[FeedbackCategory]
    INTERRUPTION: ClassVar[FeedbackCategory]
    CANCELLATION: ClassVar[FeedbackCategory]
    FAILURE: ClassVar[FeedbackCategory]
    TIMEOUT: ClassVar[FeedbackCategory]
    VALIDATION: ClassVar[FeedbackCategory]
    QUALITY: ClassVar[FeedbackCategory]
    UNCERTAINTY: ClassVar[FeedbackCategory]
    REQUEST_FOR_HUMAN: ClassVar[FeedbackCategory]
    PARTIAL_RESULT: ClassVar[FeedbackCategory]
    COMPLETION: ClassVar[FeedbackCategory]


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
