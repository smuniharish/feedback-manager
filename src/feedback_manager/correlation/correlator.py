"""The default correlation strategy."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from feedback_manager.core.events import FeedbackEvent


def default_correlation_id(feedback: FeedbackEvent) -> str:
    """Return the most specific execution identifier of ``feedback``, or its own ID.

    The first available of ``run_id``, ``thread_id``, and ``checkpoint_id``
    wins, so feedback about the same run (or thread, or checkpoint) shares one
    correlation ID. Feedback without execution context is its own group: its
    correlation ID is its ``feedback_id``.
    """
    context = feedback.execution_context
    if context is not None:
        for candidate in (context.run_id, context.thread_id, context.checkpoint_id):
            if candidate is not None:
                return candidate
    return str(feedback.feedback_id)


class DefaultFeedbackCorrelator:
    """Groups feedback by run, then thread, then checkpoint; see `default_correlation_id`."""

    async def correlate(self, feedback: FeedbackEvent) -> str:
        """Return the correlation ID for ``feedback``."""
        return default_correlation_id(feedback)


__all__ = ["DefaultFeedbackCorrelator", "default_correlation_id"]
