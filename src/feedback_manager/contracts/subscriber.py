"""Structural contract for push-based feedback subscribers."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from feedback_manager.core.events import FeedbackEvent


@runtime_checkable
class FeedbackSubscriber(Protocol):
    """A callable notified whenever a feedback event is created or transitions.

    Any ``async def __call__(self, feedback: FeedbackEvent) -> None`` object,
    including a plain async function, satisfies this protocol -- there is no
    need to subclass anything to subscribe.
    """

    async def __call__(self, feedback: FeedbackEvent) -> None: ...


__all__ = ["FeedbackSubscriber"]
