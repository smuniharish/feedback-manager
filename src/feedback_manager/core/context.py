"""Execution and correlation context: how feedback ties back to a run.

``ExecutionContext`` is a thin bag of optional identifiers -- deliberately
*not* coupled to LangGraph or ``langgraph_xai`` internal types. Integration
adapters (``feedback_manager.integrations.*``) are responsible for
extracting these identifiers from framework-native objects (a LangGraph
``RunnableConfig``, a ``langgraph_xai`` ``ExecutionContext``, ...).

``CorrelationContext`` links a feedback event to other feedback events and/or
an ``ExecutionContext``. Almost every field on both models is optional:
feedback should be usable even when only partial correlation information is
available (e.g. a human comment with no execution context at all).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ExecutionContext(BaseModel):
    """Identifiers describing the execution that produced a feedback target."""

    model_config = ConfigDict(frozen=True)

    application_id: str | None = None
    tenant_id: str | None = None
    graph_id: str | None = None
    thread_id: str | None = None
    run_id: str | None = None
    checkpoint_id: str | None = None
    node_id: str | None = None
    task_id: str | None = None
    message_id: str | None = None
    tool_call_id: str | None = None
    generation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_empty(self) -> bool:
        """Return ``True`` if no identifying field was set."""
        return (
            all(value is None for field, value in self if field != "metadata") and not self.metadata
        )


class CorrelationContext(BaseModel):
    """Links a feedback event to other feedback and/or an execution context."""

    model_config = ConfigDict(frozen=True)

    correlation_id: str | None = None
    parent_feedback_id: UUID | None = None
    related_feedback_ids: tuple[UUID, ...] = Field(default_factory=tuple)
    execution: ExecutionContext | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = ["CorrelationContext", "ExecutionContext"]
