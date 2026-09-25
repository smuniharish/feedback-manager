"""Extract :class:`ExecutionContext` from LangGraph-native objects.

This module deliberately does not import LangGraph's ``StateGraph``/graph
runtime types -- it only reads the plain-dict ``RunnableConfig`` shape that
every LangGraph/LangChain ``Runnable`` invocation already carries, plus the
``xai_*`` metadata keys that ``langgraph-xai``'s ``XAIRuntime`` recognizes
(``xai_application_id``, ``xai_tenant_id``, ``xai_graph_id``), so the two
integrations agree on the same identifiers without any hard coupling.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from feedback_manager.core.context import ExecutionContext


def execution_context_from_config(
    config: Mapping[str, Any] | None,
    *,
    node_id: str | None = None,
    tool_call_id: str | None = None,
    generation_id: str | None = None,
    message_id: str | None = None,
) -> ExecutionContext:
    """Build an :class:`ExecutionContext` from a LangGraph/LangChain ``RunnableConfig``.

    Any of ``node_id``/``tool_call_id``/``generation_id``/``message_id`` that
    the caller already knows (e.g. from inside a node function, or from a
    LangChain callback) are layered on top of what can be recovered from
    ``config`` alone.
    """
    config = config or {}
    configurable: Mapping[str, Any] = config.get("configurable", {}) or {}
    metadata: Mapping[str, Any] = config.get("metadata", {}) or {}
    run_id = config.get("run_id")

    return ExecutionContext(
        application_id=metadata.get("xai_application_id"),
        tenant_id=metadata.get("xai_tenant_id"),
        graph_id=metadata.get("xai_graph_id"),
        thread_id=configurable.get("thread_id"),
        run_id=str(run_id) if run_id is not None else configurable.get("run_id"),
        checkpoint_id=configurable.get("checkpoint_id"),
        node_id=node_id,
        task_id=configurable.get("task_id"),
        message_id=message_id,
        tool_call_id=tool_call_id,
        generation_id=generation_id,
    )


__all__ = ["execution_context_from_config"]
