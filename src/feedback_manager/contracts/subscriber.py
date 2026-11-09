"""The subscriber contract: push notifications for feedback changes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from feedback_manager.core.events import FeedbackEvent


class FeedbackSubscriber(ABC):
    """Called with every newly received event and every lifecycle change.

    Subclass it for stateful subscribers. `FeedbackManager.subscribe` also
    accepts a plain ``async def`` function and wraps it in `FunctionSubscriber`.
    """

    @abstractmethod
    async def __call__(self, feedback: FeedbackEvent) -> None:
        """Receive ``feedback`` in its new state."""


class FunctionSubscriber(FeedbackSubscriber):
    """Adapt an ``async def`` function to `FeedbackSubscriber`.

    Args:
        function: Coroutine function called with each event.
    """

    __slots__ = ("function",)

    def __init__(self, function: Callable[[FeedbackEvent], Awaitable[None]]) -> None:
        self.function = function

    async def __call__(self, feedback: FeedbackEvent) -> None:
        """Forward ``feedback`` to the wrapped function."""
        await self.function(feedback)

    def __repr__(self) -> str:
        return f"FunctionSubscriber({self.function!r})"


__all__ = ["FeedbackSubscriber", "FunctionSubscriber"]
