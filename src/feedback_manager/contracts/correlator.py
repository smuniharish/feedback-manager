"""The correlator contract: how feedback is grouped."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from feedback_manager.core.events import FeedbackEvent


class FeedbackCorrelator(ABC):
    """Assigns the correlation ID that groups related feedback.

    `FeedbackManager` calls it once per submission. Feedback that shares a
    correlation ID can be fetched together with
    ``FeedbackQuery(correlation_id=...)``. `DefaultFeedbackCorrelator` groups
    feedback by run, then thread, then checkpoint.
    """

    @abstractmethod
    async def correlate(self, feedback: FeedbackEvent) -> str:
        """Return the correlation ID for ``feedback``: a non-empty string."""


__all__ = ["FeedbackCorrelator"]
