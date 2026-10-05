"""What a piece of feedback is about."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from feedback_manager.core._types import Identifier, JsonObject, OpenStringValue


class FeedbackTargetType(OpenStringValue):
    """The kind of thing feedback is about.

    The constants are the well-known target types; any other non-empty string
    is a valid target type too, for example ``FeedbackTargetType("document")``.
    """

    __slots__ = ()

    APPLICATION: ClassVar[FeedbackTargetType]
    """The application as a whole."""
    AGENT: ClassVar[FeedbackTargetType]
    """An agent."""
    GRAPH: ClassVar[FeedbackTargetType]
    """A LangGraph graph."""
    THREAD: ClassVar[FeedbackTargetType]
    """A conversation thread (a LangGraph ``thread_id``)."""
    RUN: ClassVar[FeedbackTargetType]
    """One execution of a graph or chain."""
    CHECKPOINT: ClassVar[FeedbackTargetType]
    """A LangGraph checkpoint."""
    NODE: ClassVar[FeedbackTargetType]
    """A graph node."""
    TASK: ClassVar[FeedbackTargetType]
    """A LangGraph task."""
    TOOL_CALL: ClassVar[FeedbackTargetType]
    """One call of a tool."""
    TOOL_RESULT: ClassVar[FeedbackTargetType]
    """The result a tool call returned."""
    GENERATION: ClassVar[FeedbackTargetType]
    """One model generation."""
    MESSAGE: ClassVar[FeedbackTargetType]
    """One message."""
    STATE: ClassVar[FeedbackTargetType]
    """Graph state."""


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
    """What a `FeedbackEvent` is about: a target type and an identifier of that type.

    Attributes:
        type: The kind of thing the feedback is about.
        id: The identifier of the target, such as a tool call ID or a generation ID.
        metadata: JSON-compatible details about the target.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    type: FeedbackTargetType
    id: Identifier
    metadata: JsonObject = Field(default_factory=dict)


__all__ = ["FeedbackTarget", "FeedbackTargetType"]
