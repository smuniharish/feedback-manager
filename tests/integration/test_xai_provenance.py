"""Integration tests: real ``langgraph-xai`` instrumentation feeding provenance."""

from __future__ import annotations

from typing import TypedDict

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph_xai import XAIRuntime

from feedback_manager import (
    FeedbackCategory,
    FeedbackManager,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.integrations.xai import XAIProvenanceAdapter

pytestmark = pytest.mark.integration


class _State(TypedDict):
    value: str


async def test_provenance_is_attached_during_active_run() -> None:
    runtime = XAIRuntime(application_id="test-app", tenant_id="test-tenant", graph_id="test-graph")
    adapter = XAIProvenanceAdapter(runtime)
    manager = FeedbackManager(provenance_adapter=adapter)
    captured: list[object] = []

    async def node(state: _State) -> _State:
        feedback = await manager.submit(
            source=FeedbackSource.AGENT,
            category=FeedbackCategory.COMPLETION,
            target=FeedbackTarget(type=FeedbackTargetType.NODE, id="node"),
            payload={"ok": True},
        )
        captured.append(feedback)
        return {"value": "done"}

    graph = StateGraph(_State)
    graph.add_node("node", node)
    graph.add_edge(START, "node")
    graph.add_edge("node", END)
    compiled = graph.compile()
    instrumented = runtime.instrument(compiled)

    await instrumented.ainvoke({"value": ""}, config={"configurable": {"thread_id": "prov-1"}})

    assert len(captured) == 1
    feedback = captured[0]
    assert feedback.provenance is not None
    assert feedback.provenance.execution_id


async def test_provenance_adapter_returns_none_outside_a_run() -> None:
    runtime = XAIRuntime(application_id="test-app", tenant_id="test-tenant", graph_id="test-graph")
    adapter = XAIProvenanceAdapter(runtime)
    manager = FeedbackManager(provenance_adapter=adapter)

    feedback = await manager.submit(
        source=FeedbackSource.EVALUATOR,
        category=FeedbackCategory.QUALITY,
        target=FeedbackTarget(type=FeedbackTargetType.GRAPH, id="test-graph"),
        payload={"score": 0.9},
    )
    assert feedback.provenance is None


async def test_provenance_can_be_resolved_after_run_via_provenance_store() -> None:
    from feedback_manager.core.context import CorrelationContext, ExecutionContext

    runtime = XAIRuntime(application_id="test-app", tenant_id="test-tenant", graph_id="test-graph")
    adapter = XAIProvenanceAdapter(runtime)

    run_id: str | None = None

    async def node(state: _State) -> _State:
        nonlocal run_id
        run = runtime.current_run
        if run is not None:
            run_id = str(run.execution.id)
        return {"value": "done"}

    graph = StateGraph(_State)
    graph.add_node("node", node)
    graph.add_edge(START, "node")
    graph.add_edge("node", END)
    instrumented = runtime.instrument(graph.compile())

    await instrumented.ainvoke({"value": ""}, config={"configurable": {"thread_id": "prov-2"}})
    assert run_id is not None

    correlation = CorrelationContext(execution=ExecutionContext(run_id=run_id))
    reference = await adapter.resolve(correlation)
    assert reference is not None
    assert reference.execution_id == run_id
