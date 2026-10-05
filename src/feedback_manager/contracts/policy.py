"""Policy contracts: business rules on transitions, and redaction before storage."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from feedback_manager.core.events import FeedbackEvent
    from feedback_manager.core.status import FeedbackStatus


class FeedbackLifecyclePolicy(ABC):
    """Adds business rules on top of the lifecycle state machine.

    The state machine only decides which moves are structurally legal. A
    lifecycle policy can deny a legal move for business reasons, such as "only
    a reviewer may resolve feedback". `FeedbackManager` asks the policy before
    every transition and re-asks it if the event changed concurrently.
    """

    @abstractmethod
    def authorize_transition(self, feedback: FeedbackEvent, target: FeedbackStatus) -> None:
        """Allow a transition by returning normally.

        Raises:
            FeedbackLifecycleError: To deny moving ``feedback`` to ``target``.
        """


class FeedbackRedactionPolicy(ABC):
    """Removes or masks sensitive data before feedback is correlated and stored."""

    @abstractmethod
    def redact(self, feedback: FeedbackEvent) -> FeedbackEvent:
        """Return ``feedback`` itself, or a redacted copy of it."""


__all__ = ["FeedbackLifecyclePolicy", "FeedbackRedactionPolicy"]
