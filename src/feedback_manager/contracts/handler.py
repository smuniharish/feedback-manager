"""The handler contract: something that reacts to new feedback."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from feedback_manager.core.events import FeedbackEvent


@dataclass(frozen=True, slots=True)
class FeedbackHandlerResult:
    """The outcome of one handler invocation.

    Attributes:
        handled: Whether the handler acted on the event. ``False`` means the
            handler deliberately did nothing, which is not a failure.
        detail: An optional human-readable note about what the handler did.
    """

    handled: bool
    detail: str | None = None


class FeedbackHandler(ABC):
    """Reacts to a newly received `FeedbackEvent`.

    The `FeedbackRouter` selects handlers for each event, and `FeedbackManager`
    runs them one after another. Return a result for every business outcome,
    including "not applicable"; raise only for genuine failures, which the
    manager isolates according to its `FailurePolicy`.
    """

    @abstractmethod
    async def handle(self, feedback: FeedbackEvent) -> FeedbackHandlerResult:
        """React to ``feedback`` and report the outcome."""


__all__ = ["FeedbackHandler", "FeedbackHandlerResult"]
