"""The execution a piece of feedback refers to."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from feedback_manager.core._types import Identifier, JsonObject


class ExecutionContext(BaseModel):
    """Identifiers of the execution that produced the feedback target.

    Every field is optional: feedback stays useful with partial context, such
    as a human comment that carries no run information at all. The helpers in
    `feedback_manager.integrations.frameworks` fill these fields from an
    agent framework, for example a LangGraph config or state snapshot, or a
    plain mapping from a custom agent.

    Attributes:
        application_id: The application, as used by ``agxai``.
        tenant_id: The tenant, as used by ``agxai``.
        graph_id: The graph, as used by ``agxai``.
        agent_id: The agent that produced the target, which tells agents of
            a multi-agent system apart.
        thread_id: The LangGraph thread.
        run_id: The run. Inside an ``agxai`` instrumented call this is
            the ``agxai`` run ID, which provenance resolution uses.
        checkpoint_id: The LangGraph checkpoint.
        node_id: The graph node.
        task_id: The LangGraph task.
        message_id: The message.
        tool_call_id: The tool call.
        generation_id: The model generation.
        interrupt_id: The LangGraph interrupt the feedback answers.
        metadata: JSON-compatible details about the execution.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    application_id: Identifier | None = None
    tenant_id: Identifier | None = None
    graph_id: Identifier | None = None
    agent_id: Identifier | None = None
    thread_id: Identifier | None = None
    run_id: Identifier | None = None
    checkpoint_id: Identifier | None = None
    node_id: Identifier | None = None
    task_id: Identifier | None = None
    message_id: Identifier | None = None
    tool_call_id: Identifier | None = None
    generation_id: Identifier | None = None
    interrupt_id: Identifier | None = None
    metadata: JsonObject = Field(default_factory=dict)


__all__ = ["ExecutionContext"]
