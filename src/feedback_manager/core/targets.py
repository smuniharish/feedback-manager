"""Feedback targets: what a piece of feedback is *about*.

``FeedbackTargetType`` follows the same open ``str`` pattern as
``FeedbackSource``/``FeedbackCategory``. ``FeedbackTarget`` pairs a type with
an opaque identifier string, deliberately *not* coupled to any LangGraph
internal type -- integration adapters translate LangGraph-native identifiers
(node ids, tool call ids, checkpoint ids, ...) into ``FeedbackTarget``
instances at the boundary.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from feedback_manager.core._open_value import OpenStringValue


class FeedbackTargetType(OpenStringValue):
    """Identifies the *kind* of thing feedback was given about."""

    __slots__ = ()

    APPLICATION: ClassVar[FeedbackTargetType]
    AGENT: ClassVar[FeedbackTargetType]
    GRAPH: ClassVar[FeedbackTargetType]
    THREAD: ClassVar[FeedbackTargetType]
    RUN: ClassVar[FeedbackTargetType]
    CHECKPOINT: ClassVar[FeedbackTargetType]
    NODE: ClassVar[FeedbackTargetType]
    TASK: ClassVar[FeedbackTargetType]
    TOOL_CALL: ClassVar[FeedbackTargetType]
    TOOL_RESULT: ClassVar[FeedbackTargetType]
    GENERATION: ClassVar[FeedbackTargetType]
    MESSAGE: ClassVar[FeedbackTargetType]
    STATE: ClassVar[FeedbackTargetType]


FeedbackTargetType.APPLICATION = FeedbackTargetType("application")
FeedbackTargetType.AGENT = FeedbackTargetType("agent")
FeedbackTargetType.GRAPH = FeedbackTargetType("graph")
FeedbackTargetType.THREAD = FeedbackTargetType("thread")
FeedbackTargetType.RUN = FeedbackTargetType("run")
FeedbackTargetType.CHECKPOINT = FeedbackTargetType("checkpoint")
FeedbackTargetType.NODE = FeedbackTargetType("node")
FeedbackTargetType.TASK = FeedbackTargetType("task")
FeedbackTargetType.TOOL_CALL = FeedbackTargetType("tool_call")
FeedbackTargetType.TOOL_RESULT = FeedbackTargetType("tool_result")
FeedbackTargetType.GENERATION = FeedbackTargetType("generation")
FeedbackTargetType.MESSAGE = FeedbackTargetType("message")
FeedbackTargetType.STATE = FeedbackTargetType("state")


class FeedbackTarget(BaseModel):
    """What a :class:`~feedback_manager.core.events.FeedbackEvent` is about."""

    model_config = ConfigDict(frozen=True)

    type: FeedbackTargetType
    id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = ["FeedbackTarget", "FeedbackTargetType"]
