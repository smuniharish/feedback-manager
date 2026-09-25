"""Abstract contract for feedback handlers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from feedback_manager.core.context import CorrelationContext
from feedback_manager.core.events import FeedbackEvent


@dataclass(frozen=True, slots=True)
class FeedbackContext:
    """Ambient context passed to a handler alongside the feedback event."""

    correlation: CorrelationContext | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FeedbackHandlerResult:
    """The outcome of a single handler invocation."""

    handled: bool
    detail: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class FeedbackHandler(ABC):
    """Abstract contract for something that reacts to a :class:`FeedbackEvent`.

    Handlers are independently replaceable and must not raise for
    business-as-usual outcomes (e.g. "feedback rejected") -- only for
    genuine handler failures, which the router/manager isolate per the
    configured :class:`~feedback_manager.policies.failure.FailurePolicy`.
    """

    @abstractmethod
    async def handle(
        self, feedback: FeedbackEvent, context: FeedbackContext
    ) -> FeedbackHandlerResult:
        """React to ``feedback`` and report the outcome."""


__all__ = ["FeedbackContext", "FeedbackHandler", "FeedbackHandlerResult"]
