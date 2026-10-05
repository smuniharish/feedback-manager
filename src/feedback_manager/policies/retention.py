"""Retention: when pending feedback should expire.

feedback-manager runs no background scheduler. `RetentionPolicy` decides which
pending events are overdue, and `FeedbackManager.expire_overdue` expires them
when your application calls it, for example from a periodic job.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from feedback_manager.core.lifecycle import is_legal_transition
from feedback_manager.core.status import FeedbackStatus
from feedback_manager.errors import FeedbackConfigurationError, FeedbackValidationError

if TYPE_CHECKING:
    from feedback_manager.core.events import FeedbackEvent


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """How long feedback may stay pending before it expires.

    Only events in a status that can legally move to ``EXPIRED`` (``CREATED``,
    ``RECEIVED``, or ``ACKNOWLEDGED``) expire; ``HANDLED`` feedback waits for
    its resolution.

    Attributes:
        max_pending_age: The longest an event may stay pending, measured from
            its ``created_at``.

    Raises:
        FeedbackConfigurationError: If ``max_pending_age`` is not positive.
    """

    max_pending_age: timedelta

    def __post_init__(self) -> None:
        if self.max_pending_age <= timedelta(0):
            raise FeedbackConfigurationError("max_pending_age must be a positive timedelta")

    def cutoff(self, now: datetime | None = None) -> datetime:
        """Return the creation time before which pending events are overdue.

        Raises:
            FeedbackValidationError: If ``now`` is naive.
        """
        return _aware(now) - self.max_pending_age

    def is_expired(self, feedback: FeedbackEvent, *, now: datetime | None = None) -> bool:
        """Return whether ``feedback`` is overdue and may move to ``EXPIRED``.

        Raises:
            FeedbackValidationError: If ``now`` is naive.
        """
        return (
            feedback.status is not FeedbackStatus.EXPIRED
            and is_legal_transition(feedback.status, FeedbackStatus.EXPIRED)
            and feedback.created_at < self.cutoff(now)
        )


def _aware(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if now.utcoffset() is None:
        raise FeedbackValidationError("now must be timezone-aware")
    return now


__all__ = ["RetentionPolicy"]
