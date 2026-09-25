"""Feedback lifecycle status.

Unlike source/category/target, the feedback lifecycle *is* a closed set of
states with well-defined legal transitions between them -- see
:mod:`feedback_manager.core.lifecycle`. A plain, closed ``StrEnum`` is the
right tool here.
"""

from __future__ import annotations

from enum import StrEnum


class FeedbackStatus(StrEnum):
    """The lifecycle state of a :class:`~feedback_manager.core.events.FeedbackEvent`."""

    CREATED = "created"
    RECEIVED = "received"
    ACKNOWLEDGED = "acknowledged"
    HANDLED = "handled"
    RESOLVED = "resolved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


TERMINAL_STATUSES: frozenset[FeedbackStatus] = frozenset(
    {
        FeedbackStatus.RESOLVED,
        FeedbackStatus.REJECTED,
        FeedbackStatus.CANCELLED,
        FeedbackStatus.EXPIRED,
    }
)

__all__ = ["TERMINAL_STATUSES", "FeedbackStatus"]
