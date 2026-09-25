"""Open, extensible identifier for who or what produced a piece of feedback.

``FeedbackSource`` is intentionally a plain ``str`` subclass rather than a
closed ``enum.Enum``: the well-known sources below are common cases, not an
exhaustive list. Application code can construct ``FeedbackSource("custom")``
for a source this package has never heard of, and it behaves exactly like
any other string (equality, hashing, JSON/pydantic serialization) everywhere
a ``FeedbackSource`` is expected.
"""

from __future__ import annotations

from typing import ClassVar

from feedback_manager.core._open_value import OpenStringValue


class FeedbackSource(OpenStringValue):
    """Identifies the origin of a :class:`~feedback_manager.core.events.FeedbackEvent`."""

    __slots__ = ()

    HUMAN: ClassVar[FeedbackSource]
    AGENT: ClassVar[FeedbackSource]
    GENERATION: ClassVar[FeedbackSource]
    TOOL: ClassVar[FeedbackSource]
    EVALUATOR: ClassVar[FeedbackSource]
    APPLICATION: ClassVar[FeedbackSource]
    SYSTEM: ClassVar[FeedbackSource]
    EXTERNAL: ClassVar[FeedbackSource]


FeedbackSource.HUMAN = FeedbackSource("human")
FeedbackSource.AGENT = FeedbackSource("agent")
FeedbackSource.GENERATION = FeedbackSource("generation")
FeedbackSource.TOOL = FeedbackSource("tool")
FeedbackSource.EVALUATOR = FeedbackSource("evaluator")
FeedbackSource.APPLICATION = FeedbackSource("application")
FeedbackSource.SYSTEM = FeedbackSource("system")
FeedbackSource.EXTERNAL = FeedbackSource("external")

__all__ = ["FeedbackSource"]
