"""Abstract contracts for feedback and lifecycle policy hooks."""

from __future__ import annotations

from abc import ABC, abstractmethod

from feedback_manager.core.events import FeedbackEvent
from feedback_manager.core.status import FeedbackStatus


class FeedbackLifecyclePolicy(ABC):
    """Allows application code to add business rules on top of the base state machine.

    The base transition table in :mod:`feedback_manager.core.lifecycle`
    encodes structural legality only. A ``FeedbackLifecyclePolicy`` can
    reject an otherwise-legal transition for business reasons (e.g. "only
    the original requester may resolve their own feedback").
    """

    @abstractmethod
    def authorize_transition(self, feedback: FeedbackEvent, target: FeedbackStatus) -> None:
        """Raise if the transition should not be allowed; return normally otherwise."""


class FeedbackPolicy(ABC):
    """General extension point for redaction/filtering before persistence or serialization."""

    @abstractmethod
    def apply(self, feedback: FeedbackEvent) -> FeedbackEvent:
        """Return a (possibly modified) copy of ``feedback``."""


__all__ = ["FeedbackLifecyclePolicy", "FeedbackPolicy"]
