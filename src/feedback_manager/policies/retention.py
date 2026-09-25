"""Retention policy: when a feedback event that never resolved should expire.

This is deliberately small: FeedbackManager does not implement a background
scheduler (Section 26 -- no custom scheduler). Instead, ``RetentionPolicy``
is a pure predicate that the manager (or an application-owned periodic task)
can use to decide whether a non-terminal event should be transitioned to
``EXPIRED``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from feedback_manager.core.events import FeedbackEvent
from feedback_manager.core.status import TERMINAL_STATUSES


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """Defines how long a non-terminal feedback event may remain pending."""

    max_pending_age: timedelta | None = None

    def is_expired(self, feedback: FeedbackEvent, *, now: datetime | None = None) -> bool:
        """Return whether ``feedback`` should be considered expired."""
        if self.max_pending_age is None:
            return False
        if feedback.status in TERMINAL_STATUSES:
            return False
        current_time = now or datetime.now(UTC)
        return current_time - feedback.created_at > self.max_pending_age


__all__ = ["RetentionPolicy"]
