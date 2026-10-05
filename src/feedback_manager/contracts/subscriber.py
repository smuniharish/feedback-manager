"""The subscriber contract: push notifications for feedback changes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from feedback_manager.core.events import FeedbackEvent


@runtime_checkable
class FeedbackSubscriber(Protocol):
    """Called with every newly received event and every lifecycle change.

    Any ``async def`` function or object with that ``__call__`` signature
    qualifies; nothing needs to be subclassed.
    """

    async def __call__(self, feedback: FeedbackEvent) -> None:
        """Receive ``feedback`` in its new state."""
        ...


__all__ = ["FeedbackSubscriber"]
