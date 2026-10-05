"""Building an `ExecutionContext` from LangGraph and LangChain objects.

The helpers read the plain ``RunnableConfig`` mapping every LangChain and
LangGraph invocation carries, the metadata LangChain passes to callbacks, and
the ``StateSnapshot`` a checkpointed graph returns from ``get_state``. They
recognize the metadata ``langgraph-xai`` adds, so feedback carries the same
run ID that ``langgraph-xai`` records.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langgraph_xai import RUN_ID_METADATA_KEY

from feedback_manager.core.context import ExecutionContext
from feedback_manager.integrations._values import text_or_none

if TYPE_CHECKING:
    from collections.abc import Mapping

    from langgraph.types import StateSnapshot


def execution_context_from_config(
    config: Mapping[str, Any] | None,
    *,
    node_id: str | None = None,
    tool_call_id: str | None = None,
    generation_id: str | None = None,
    message_id: str | None = None,
    interrupt_id: str | None = None,
) -> ExecutionContext:
    """Build an `ExecutionContext` from a ``RunnableConfig`` or callback metadata.

    Inside a graph node, pass the node's ``config``; in a LangChain callback,
    pass ``{"metadata": metadata}``. Fields are read from:

    - ``run_id``: ``metadata["langgraph_xai_run_id"]``, which ``langgraph-xai``
      sets inside instrumented calls; else ``metadata["xai_run_id"]``; else the
      config's ``run_id``;
    - ``thread_id``: ``configurable["thread_id"]``, else ``metadata["thread_id"]``;
    - ``checkpoint_id``: ``configurable["checkpoint_id"]``;
    - ``node_id``: the ``node_id`` argument, else ``metadata["langgraph_node"]``;
    - ``application_id``, ``tenant_id``, ``graph_id``: the ``xai_application_id``,
      ``xai_tenant_id``, and ``xai_graph_id`` metadata keys ``langgraph-xai`` uses.

    Identifiers that are not strings, such as UUIDs, are converted to strings,
    and blank values are treated as missing.

    Args:
        config: A ``RunnableConfig``-shaped mapping, or ``None``.
        node_id: The current node, when known.
        tool_call_id: The current tool call, when known.
        generation_id: The current model generation, when known.
        message_id: The current message, when known.
        interrupt_id: The LangGraph interrupt the feedback answers, when known.
    """
    config = config or {}
    configurable: Mapping[str, Any] = config.get("configurable") or {}
    metadata: Mapping[str, Any] = config.get("metadata") or {}
    return ExecutionContext(
        application_id=text_or_none(metadata.get("xai_application_id")),
        tenant_id=text_or_none(metadata.get("xai_tenant_id")),
        graph_id=text_or_none(metadata.get("xai_graph_id")),
        thread_id=text_or_none(configurable.get("thread_id"))
        or text_or_none(metadata.get("thread_id")),
        run_id=text_or_none(metadata.get(RUN_ID_METADATA_KEY))
        or text_or_none(metadata.get("xai_run_id"))
        or text_or_none(config.get("run_id")),
        checkpoint_id=text_or_none(configurable.get("checkpoint_id")),
        node_id=text_or_none(node_id) or text_or_none(metadata.get("langgraph_node")),
        message_id=text_or_none(message_id),
        tool_call_id=text_or_none(tool_call_id),
        generation_id=text_or_none(generation_id),
        interrupt_id=text_or_none(interrupt_id),
    )


def execution_context_from_snapshot(snapshot: StateSnapshot) -> ExecutionContext:
    """Build an `ExecutionContext` from a paused graph's state snapshot.

    Call it after a checkpointed graph pauses, with the snapshot from
    ``graph.get_state(config)`` or ``await graph.aget_state(config)``. The
    context gets the thread and checkpoint of the snapshot and, when the graph
    is instrumented by ``langgraph-xai``, the ID of the run that paused, which
    links the feedback to that run's provenance. When exactly one interrupt
    is pending, it also gets that interrupt's ID and the node that raised it.
    """
    interrupts = snapshot.interrupts
    interrupt = interrupts[0] if len(interrupts) == 1 else None
    node_id = None
    if interrupt is not None:
        node_id = next(
            (
                task.name
                for task in snapshot.tasks
                if any(pending.id == interrupt.id for pending in task.interrupts)
            ),
            None,
        )
    return execution_context_from_config(
        {"configurable": snapshot.config.get("configurable"), "metadata": snapshot.metadata},
        node_id=node_id,
        interrupt_id=None if interrupt is None else interrupt.id,
    )


__all__ = ["execution_context_from_config", "execution_context_from_snapshot"]
