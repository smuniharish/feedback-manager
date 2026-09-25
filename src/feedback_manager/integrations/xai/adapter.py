"""The *only* module in feedback_manager that imports ``langgraph_xai`` types.

``XAIProvenanceAdapter`` implements
:class:`feedback_manager.contracts.provenance.FeedbackProvenanceAdapter` by
reading from a ``langgraph_xai.XAIRuntime`` -- either the run that is
currently active on the calling task (``runtime.current_run``, when
feedback is submitted synchronously during graph execution) or, for
feedback submitted after the fact, by querying the runtime's registered
``ProvenanceStore`` for the ``Execution`` matching the feedback's
``run_id``. It never re-implements provenance capture itself; it only
translates what ``langgraph-xai`` already captured into
:class:`~feedback_manager.core.provenance.FeedbackProvenanceReference`.
"""

from __future__ import annotations

from langgraph_xai import Execution, ProvenanceStore, XAIRuntime

from feedback_manager.core.context import CorrelationContext
from feedback_manager.core.provenance import FeedbackProvenanceReference

PROVIDER_NAME = "langgraph-xai"


class XAIProvenanceAdapter:
    """Resolves :class:`FeedbackProvenanceReference` from a ``langgraph-xai`` runtime."""

    def __init__(self, runtime: XAIRuntime) -> None:
        self._runtime = runtime

    async def resolve(self, correlation: CorrelationContext) -> FeedbackProvenanceReference | None:
        execution = await self._load_execution(correlation)
        if execution is None:
            return None
        return self._map_execution(execution, correlation)

    async def _load_execution(self, correlation: CorrelationContext) -> Execution | None:
        run = self._runtime.current_run
        if run is not None:
            return run.execution

        execution_context = correlation.execution
        run_id = execution_context.run_id if execution_context else None
        if run_id is None:
            return None

        # ``ProvenanceStore`` is a Protocol; mypy flags passing it as a `type[T]`
        # registry key as "abstract", but this is exactly how langgraph-xai's
        # own Registry API is meant to be used for Protocol-typed capabilities.
        store = self._runtime.registry.get(ProvenanceStore)  # type: ignore[type-abstract]
        if store is None:
            return None
        item = await store.get(run_id)
        return item if isinstance(item, Execution) else None

    @staticmethod
    def _map_execution(
        execution: Execution, correlation: CorrelationContext
    ) -> FeedbackProvenanceReference:
        execution_context = correlation.execution
        tool_call_id = execution_context.tool_call_id if execution_context else None
        node_id = execution_context.node_id if execution_context else None

        matched_tool = None
        if tool_call_id is not None:
            matched_tool = next(
                (tool for tool in execution.tools if tool.tool_call_id == tool_call_id), None
            )

        matched_node = None
        if node_id is not None:
            matched_node = next((node for node in execution.nodes if node.node_id == node_id), None)

        latest_interaction = (
            execution.human_interactions[-1] if execution.human_interactions else None
        )

        summary = f"execution status={execution.status.value}, nodes={len(execution.nodes)}"
        if matched_node is not None:
            summary += f", matched_node_status={matched_node.status.value}"

        return FeedbackProvenanceReference(
            provider=PROVIDER_NAME,
            execution_id=str(execution.id),
            tool_execution_id=str(matched_tool.id) if matched_tool is not None else None,
            human_interaction_id=str(latest_interaction.id)
            if latest_interaction is not None
            else None,
            summary=summary,
            metadata={
                "node_count": len(execution.nodes),
                "tool_count": len(execution.tools),
                "human_interaction_count": len(execution.human_interactions),
                "status": execution.status.value,
            },
        )


__all__ = ["XAIProvenanceAdapter"]
