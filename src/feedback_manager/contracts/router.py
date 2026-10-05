"""The router contract: which handlers see an event."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from feedback_manager.contracts.handler import FeedbackHandler
    from feedback_manager.core.events import FeedbackEvent


class FeedbackRouter(ABC):
    """Selects the `FeedbackHandler` instances that should process an event.

    Routing may use any attribute of the event. It only selects handlers and
    must not invoke them: `FeedbackManager` runs the selected handlers, so each
    failure can be isolated according to the `FailurePolicy`.
    """

    @abstractmethod
    async def route(self, feedback: FeedbackEvent) -> Sequence[FeedbackHandler]:
        """Return the handlers for ``feedback``, in the order they should run."""


__all__ = ["FeedbackRouter"]
