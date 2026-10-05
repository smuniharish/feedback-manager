"""Resolving feedback provenance from ``langgraph-xai``.

This is the only module that reads ``langgraph-xai`` records. It translates
what ``langgraph-xai`` captured into a `FeedbackProvenanceReference`; it never
captures provenance itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from langgraph_xai import Execution, ProvenanceStore, StoreFilter

from feedback_manager.core.provenance import FeedbackProvenanceReference

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langgraph_xai import Decision, Evidence, XAIRuntime

    from feedback_manager.core.context import ExecutionContext
    from feedback_manager.core.events import FeedbackEvent

_PAGE_SIZE = 100


class XAIProvenanceAdapter:
    """Builds `FeedbackProvenanceReference` objects from a ``langgraph_xai.XAIRuntime``.

    `FeedbackManager` creates one when it is given an ``xai_runtime``; build
    one yourself to resolve provenance for an existing event, for example
    after loading it from a store.

    Resolution follows the feedback's ``ExecutionContext.run_id``:

    - Submitted inside an instrumented call, without a ``run_id`` or with the
      active run's ID: the active run is used, including the latest decision
      and its evidence.
    - With the ``run_id`` of another run: that run's latest ``Execution`` is
      read from the runtime's provenance store. Decisions and evidence are not
      stored by ``langgraph-xai``, so they are not part of the reference. The
      store must accept ``langgraph_xai.StoreFilter`` queries, as the bundled
      in-memory store does.

    Within the run, the reference points at the latest execution of the
    context's ``node_id``, the tool execution with its ``tool_call_id``, and
    the human interaction recorded for its ``interrupt_id``.
    """

    def __init__(self, runtime: XAIRuntime) -> None:
        self._runtime = runtime

    async def resolve(self, feedback: FeedbackEvent) -> FeedbackProvenanceReference | None:
        """Return the provenance of ``feedback``, or ``None`` when no run matches."""
        context = feedback.execution_context
        requested = _run_id(context)
        run = self._runtime.current_run
        if run is not None and requested in (None, run.run_id):
            return _reference(run.execution, context, run.decisions, run.evidence)
        if requested is None:
            return None
        execution = await self._stored_execution(requested, context)
        if execution is None:
            return None
        return _reference(execution, context, (), ())

    async def _stored_execution(
        self, run_id: UUID, context: ExecutionContext | None
    ) -> Execution | None:
        store = self._runtime.registry.get(ProvenanceStore)
        if store is None:
            return None
        application_id = (
            context.application_id
            if context is not None and context.application_id is not None
            else self._runtime.application_id
        )
        tenant_id = (
            context.tenant_id
            if context is not None and context.tenant_id is not None
            else self._runtime.tenant_id
        )
        latest: Execution | None = None
        offset = 0
        while True:
            page = [
                item
                async for item in store.query(
                    StoreFilter(
                        application_id=application_id,
                        tenant_id=tenant_id,
                        run_id=run_id,
                        item_type=Execution,
                        limit=_PAGE_SIZE,
                        offset=offset,
                    )
                )
            ]
            for item in page:
                if isinstance(item, Execution):
                    latest = item
            if len(page) < _PAGE_SIZE:
                return latest
            offset += _PAGE_SIZE


def _run_id(context: ExecutionContext | None) -> UUID | None:
    if context is None or context.run_id is None:
        return None
    try:
        return UUID(context.run_id)
    except ValueError:
        return None


def _reference(
    execution: Execution,
    context: ExecutionContext | None,
    decisions: Sequence[Decision],
    evidence: Sequence[Evidence],
) -> FeedbackProvenanceReference:
    node_id = None if context is None else context.node_id
    tool_call_id = None if context is None else context.tool_call_id
    interrupt_id = None if context is None else context.interrupt_id
    nodes = [node for node in execution.nodes if node_id is not None and node.node_id == node_id]
    tools = [
        tool
        for tool in execution.tools
        if tool_call_id is not None and tool.tool_call_id == tool_call_id
    ]
    interactions = [
        interaction
        for interaction in execution.human_interactions
        if interrupt_id is not None and interaction.request_reference == interrupt_id
    ]
    decision = decisions[-1] if decisions else None
    evidence_ids = (
        tuple(str(item) for item in decision.evidence_ids)
        if decision is not None
        else tuple(str(item.id) for item in evidence)
    )
    run_id = str(execution.context.run_id)
    status = execution.status.value
    return FeedbackProvenanceReference(
        run_id=run_id,
        execution_id=str(execution.id),
        summary=(
            f"langgraph-xai run {run_id} ({status}): {len(execution.nodes)} node "
            f"execution(s), {len(execution.tools)} tool execution(s)"
        ),
        node_execution_id=str(nodes[-1].id) if nodes else None,
        tool_execution_id=str(tools[-1].id) if tools else None,
        human_interaction_id=str(interactions[-1].id) if interactions else None,
        decision_id=None if decision is None else str(decision.id),
        evidence_ids=evidence_ids,
        metadata={
            "status": status,
            "graph_id": execution.context.graph_id,
            "thread_id": execution.context.thread_id,
            "continuation_of": (
                None if execution.continuation_of is None else str(execution.continuation_of)
            ),
            "node_count": len(execution.nodes),
            "tool_count": len(execution.tools),
            "human_interaction_count": len(execution.human_interactions),
        },
    )


__all__ = ["XAIProvenanceAdapter"]
