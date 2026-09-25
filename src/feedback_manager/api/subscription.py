"""Subscription handle returned by :meth:`FeedbackManager.subscribe`."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(slots=True)
class Subscription:
    """A handle that unsubscribes a :class:`FeedbackSubscriber` when cancelled."""

    _cancel: Callable[[], None]
    _active: bool = True

    def cancel(self) -> None:
        """Stop receiving further feedback notifications."""
        if self._active:
            self._cancel()
            self._active = False

    @property
    def active(self) -> bool:
        return self._active


__all__ = ["Subscription"]
