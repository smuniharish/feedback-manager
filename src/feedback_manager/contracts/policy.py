"""Structural contracts for feedback and lifecycle policy hooks."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from feedback_manager.core.events import FeedbackEvent
from feedback_manager.core.status import FeedbackStatus


@runtime_checkable
class FeedbackLifecyclePolicy(Protocol):
    """Allows application code to add business rules on top of the base state machine.

    The base transition table in :mod:`feedback_manager.core.lifecycle`
    encodes structural legality only. A ``FeedbackLifecyclePolicy`` can
    reject an otherwise-legal transition for business reasons (e.g. "only
    the original requester may resolve their own feedback").
    """

    def authorize_transition(self, feedback: FeedbackEvent, target: FeedbackStatus) -> None:
        """Raise if the transition should not be allowed; return normally otherwise."""
        ...


@runtime_checkable
class FeedbackPolicy(Protocol):
    """General extension point for redaction/filtering before persistence or serialization."""

    def apply(self, feedback: FeedbackEvent) -> FeedbackEvent:
        """Return a (possibly modified) copy of ``feedback``."""
        ...


__all__ = ["FeedbackLifecyclePolicy", "FeedbackPolicy"]
