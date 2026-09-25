"""Default correlation derivation."""

from __future__ import annotations

import uuid

from feedback_manager.core.context import CorrelationContext, ExecutionContext
from feedback_manager.core.events import FeedbackEvent


class DefaultFeedbackCorrelator:
    """Derives a :class:`CorrelationContext` from whatever context is available.

    If ``execution_context`` carries a ``run_id``/``thread_id``, that is used
    to build a stable ``correlation_id`` so that multiple feedback events
    about the same execution can be queried together
    (``FeedbackQuery(correlation_id=...)``). Otherwise a fresh random
    correlation id is generated so every event is still individually
    addressable.
    """

    async def correlate(
        self, feedback: FeedbackEvent, execution_context: ExecutionContext | None
    ) -> CorrelationContext:
        correlation_id = self._derive_correlation_id(execution_context)
        return CorrelationContext(correlation_id=correlation_id, execution=execution_context)

    @staticmethod
    def _derive_correlation_id(execution_context: ExecutionContext | None) -> str:
        if execution_context is not None:
            for candidate in (
                execution_context.run_id,
                execution_context.thread_id,
                execution_context.checkpoint_id,
            ):
                if candidate:
                    return candidate
        return str(uuid.uuid4())


__all__ = ["DefaultFeedbackCorrelator"]
