"""Who or what produced a piece of feedback."""

from __future__ import annotations

from typing import ClassVar

from feedback_manager.core._types import OpenStringValue


class FeedbackSource(OpenStringValue):
    """The origin of a `FeedbackEvent`: a person, a component, or an external system.

    The constants are the well-known sources. Any other non-empty string is a
    valid source too, for example ``FeedbackSource("mcp_server")``.
    """

    __slots__ = ()

    HUMAN: ClassVar[FeedbackSource]
    """A person, such as an end user or a reviewer."""
    AGENT: ClassVar[FeedbackSource]
    """An agent or graph, reporting on its own execution."""
    GENERATION: ClassVar[FeedbackSource]
    """A model generation, such as a failed or interrupted model call."""
    TOOL: ClassVar[FeedbackSource]
    """A tool or retriever."""
    EVALUATOR: ClassVar[FeedbackSource]
    """An automated evaluator, such as an LLM-as-judge or a rule-based checker."""
    APPLICATION: ClassVar[FeedbackSource]
    """The host application's own code."""
    SYSTEM: ClassVar[FeedbackSource]
    """Infrastructure, such as a human-in-the-loop request raised by the runtime."""
    EXTERNAL: ClassVar[FeedbackSource]
    """A third-party system, such as a ticketing or monitoring tool."""


FeedbackSource.HUMAN = FeedbackSource("human")
FeedbackSource.AGENT = FeedbackSource("agent")
FeedbackSource.GENERATION = FeedbackSource("generation")
FeedbackSource.TOOL = FeedbackSource("tool")
FeedbackSource.EVALUATOR = FeedbackSource("evaluator")
FeedbackSource.APPLICATION = FeedbackSource("application")
FeedbackSource.SYSTEM = FeedbackSource("system")
FeedbackSource.EXTERNAL = FeedbackSource("external")

__all__ = ["FeedbackSource"]
