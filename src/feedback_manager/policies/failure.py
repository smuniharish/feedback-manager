"""Failure isolation policy: feedback failures must not take down agent execution.

Every stage that can fail independently (store, routing, handler,
subscriber, serialization, provenance) is wrapped through
:meth:`FailurePolicy.run_stage`, which applies the configured
:class:`FailureMode` for that stage.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeVar

logger = logging.getLogger("feedback_manager.policies.failure")

T = TypeVar("T")


class FailureMode(StrEnum):
    """How a failure in one processing stage should be handled."""

    BEST_EFFORT = "best_effort"
    """Log the failure and continue; the caller gets ``None`` back for that stage."""

    BLOCKING = "blocking"
    """Re-raise the failure to the caller of :meth:`FeedbackManager.submit`/etc."""


class FeedbackStage(StrEnum):
    """The independently-isolatable processing stages of feedback handling."""

    STORE = "store"
    ROUTING = "routing"
    HANDLER = "handler"
    SUBSCRIBER = "subscriber"
    SERIALIZATION = "serialization"
    PROVENANCE = "provenance"


@dataclass(slots=True)
class FailurePolicy:
    """Per-stage failure isolation configuration.

    Defaults to :attr:`FailureMode.BLOCKING` for :attr:`FeedbackStage.STORE`
    (a failed persistence is almost always a caller-visible error) and
    :attr:`FailureMode.BEST_EFFORT` for every other stage (a broken handler,
    subscriber, or provenance lookup must never break feedback submission).
    """

    modes: dict[FeedbackStage, FailureMode] = field(
        default_factory=lambda: {
            FeedbackStage.STORE: FailureMode.BLOCKING,
            FeedbackStage.ROUTING: FailureMode.BEST_EFFORT,
            FeedbackStage.HANDLER: FailureMode.BEST_EFFORT,
            FeedbackStage.SUBSCRIBER: FailureMode.BEST_EFFORT,
            FeedbackStage.SERIALIZATION: FailureMode.BLOCKING,
            FeedbackStage.PROVENANCE: FailureMode.BEST_EFFORT,
        }
    )

    def mode_for(self, stage: FeedbackStage) -> FailureMode:
        return self.modes.get(stage, FailureMode.BEST_EFFORT)

    async def run_stage(
        self,
        stage: FeedbackStage,
        operation: Callable[[], Awaitable[T]],
        *,
        on_error: Callable[[FeedbackStage, BaseException], None] | None = None,
    ) -> T | None:
        """Run ``operation``, applying the configured mode for ``stage``.

        Never silently swallows errors: ``BEST_EFFORT`` still logs (and
        invokes ``on_error`` for observability) before returning ``None``.
        """
        try:
            return await operation()
        except Exception as exc:
            if on_error is not None:
                on_error(stage, exc)
            if self.mode_for(stage) is FailureMode.BLOCKING:
                raise
            logger.warning("feedback_manager stage %s failed (best-effort): %s", stage.value, exc)
            return None


__all__ = ["FailureMode", "FailurePolicy", "FeedbackStage"]
