"""Abstract contract for routing feedback events to handlers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from feedback_manager.contracts.handler import FeedbackHandler
from feedback_manager.core.events import FeedbackEvent


class FeedbackRouter(ABC):
    """Decides which :class:`FeedbackHandler` instances should see an event.

    Routing may consider any attribute of the event (source, category,
    target, execution context, lifecycle status, metadata). Implementations
    must be side-effect free -- routing only *selects* handlers, it does not
    invoke them (that is the manager's job, so failures in one handler can be
    isolated from others per the configured failure policy).
    """

    @abstractmethod
    async def route(self, feedback: FeedbackEvent) -> Sequence[FeedbackHandler]:
        """Return the ordered sequence of handlers that should process ``feedback``."""


__all__ = ["FeedbackRouter"]
