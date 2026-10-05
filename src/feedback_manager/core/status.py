"""The lifecycle status of a feedback event."""

from __future__ import annotations

from enum import StrEnum


class FeedbackStatus(StrEnum):
    """The lifecycle status of a `FeedbackEvent`.

    Unlike sources and categories, statuses are a closed set: the legal moves
    between them are defined in `LEGAL_TRANSITIONS`.
    """

    CREATED = "created"
    """Built but not yet accepted; `FeedbackManager.submit` never stores this status."""
    RECEIVED = "received"
    """Accepted and stored."""
    ACKNOWLEDGED = "acknowledged"
    """Seen by a consumer that will act on it."""
    HANDLED = "handled"
    """Processed, awaiting a final resolution."""
    RESOLVED = "resolved"
    """Closed with an outcome. Terminal."""
    REJECTED = "rejected"
    """Closed as declined or not applicable. Terminal."""
    CANCELLED = "cancelled"
    """Withdrawn before completion. Terminal."""
    EXPIRED = "expired"
    """Closed because it stayed pending for too long. Terminal."""


TERMINAL_STATUSES: frozenset[FeedbackStatus] = frozenset(
    {
        FeedbackStatus.RESOLVED,
        FeedbackStatus.REJECTED,
        FeedbackStatus.CANCELLED,
        FeedbackStatus.EXPIRED,
    }
)
"""The statuses with no outgoing transitions."""

__all__ = ["TERMINAL_STATUSES", "FeedbackStatus"]
