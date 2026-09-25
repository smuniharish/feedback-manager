"""Structural contract for deriving correlation context at submission time."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from feedback_manager.core.context import CorrelationContext, ExecutionContext
from feedback_manager.core.events import FeedbackEvent


@runtime_checkable
class FeedbackCorrelator(Protocol):
    """Derives a :class:`CorrelationContext` for an about-to-be-submitted event."""

    async def correlate(
        self, feedback: FeedbackEvent, execution_context: ExecutionContext | None
    ) -> CorrelationContext: ...


__all__ = ["FeedbackCorrelator"]
