"""Delivery mode documentation for feedback consumption.

FeedbackManager supports two consumption modes (Section 19 of the spec):
pull (``manager.get``/``manager.query``) and push
(``manager.subscribe``/``manager.stream``). ``DeliveryMode`` is a small,
descriptive enum used in observability metadata; it does not gate any
behavior by itself.
"""

from __future__ import annotations

from enum import StrEnum


class DeliveryMode(StrEnum):
    """How a consumer is receiving a feedback event."""

    PULL = "pull"
    PUSH = "push"
    STREAM = "stream"


__all__ = ["DeliveryMode"]
