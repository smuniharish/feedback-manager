"""langgraph-xai integration: provenance resolution against real runtimes and stores."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, TypedDict
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt
from langgraph_xai import (
    DecisionType,
    EvidenceType,
    Execution,
    ExecutionStatus,
    InMemoryProvenanceStore,
    ProvenanceLink,
    ProvenanceStore,
    XAIRuntime,
)
from langgraph_xai import ExecutionContext as XaiContext

from feedback_manager import (
    ExecutionContext,
    FeedbackEvent,
    FeedbackManager,
    FeedbackTarget,
)
from feedback_manager.errors import FeedbackCorrelationError
from feedback_manager.integrations.langgraph import (
    HumanInTheLoopBridge,
    execution_context_from_config,
    execution_context_from_snapshot,
    extract_interrupts,
)
from feedback_manager.integrations.xai import XAIProvenanceAdapter
from feedback_manager.policies import FailureMode, FailurePolicy, FeedbackStage
from tests.conftest import RecordingSink

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from langchain_core.runnables import RunnableConfig
    from langgraph_xai.core.protocols import StoreQuery

pytestmark = pytest.mark.integration

TARGET = FeedbackTarget(type="run", id="run")


class State(TypedDict, total=False):
    messages: list[Any]
    step: str


def _runtime() -> XAIRuntime:
    return XAIRuntime(application_id="app", tenant_id="tenant", graph_id="graph")


def _chain(*nodes: tuple[str, Any], checkpointer: Any = None) -> Any:
    builder = StateGraph(State)
    previous = START
    for name, node in nodes:
        builder.add_node(name, node)
        builder.add_edge(previous, name)
        previous = name
    builder.add_edge(previous, END)
    return builder.compile(checkpointer=checkpointer)


async def _submit(manager: FeedbackManager, context: ExecutionContext | None) -> FeedbackEvent:
    return await manager.submit(
        source="evaluator", category="quality", target=TARGET, execution_context=context
    )


@tool
async def echo(text: str) -> str:
    """Echo the text."""
    return text


class TestLiveRuns:
    async def test_feedback_carries_the_latest_decision_and_its_evidence(self) -> None:
        xai = _runtime()
        manager = FeedbackManager(xai_runtime=xai)
        recorded: dict[str, UUID] = {}
        submitted: list[FeedbackEvent] = []

        async def decide(state: State) -> State:
            evidence = await xai.record_evidence(EvidenceType.RULE, summary="amount above limit")
            await xai.record_evidence(EvidenceType.RULE, summary="unrelated")
            decision = await xai.record_decision(
                "ESCALATE",
                decision_type=DecisionType.ROUTING,
                candidate_actions=["ESCALATE", "APPROVE"],
                evidence_ids=[evidence.id],
            )
            recorded.update(decision=decision.id, evidence=evidence.id)
            return {"step": "decided"}

        async def review(state: State, config: RunnableConfig) -> State:
            context = execution_context_from_config(config, node_id="decide")
            submitted.append(await _submit(manager, context))
            return {}

        with xai.collect_runs() as runs:
            await xai.instrument(_chain(("decide", decide), ("review", review))).ainvoke({})

        reference = submitted[0].provenance
        assert reference is not None
        (decide_execution,) = [node for node in runs[0].execution.nodes if node.node_id == "decide"]
        assert reference.run_id == str(runs[0].run_id)
        assert reference.execution_id == str(runs[0].execution.id)
        assert reference.decision_id == str(recorded["decision"])
        assert reference.evidence_ids == (str(recorded["evidence"]),)
        assert reference.node_execution_id == str(decide_execution.id)
        assert reference.metadata["status"] == "running"
        assert "(running)" in reference.summary

    async def test_without_a_decision_all_recorded_evidence_is_referenced(self) -> None:
        xai = _runtime()
        manager = FeedbackManager(xai_runtime=xai)
        submitted: list[FeedbackEvent] = []

        async def gather(state: State) -> State:
            first = await xai.record_evidence(EvidenceType.MODEL_OUTPUT, summary="draft")
            second = await xai.record_evidence(EvidenceType.MODEL_OUTPUT, summary="revision")
            event = await _submit(manager, None)
            submitted.append(event)
            assert event.provenance is not None
            assert event.provenance.evidence_ids == (str(first.id), str(second.id))
            assert event.provenance.decision_id is None
            return {}

        await xai.instrument(_chain(("gather", gather))).ainvoke({})

        assert len(submitted) == 1

    async def test_non_uuid_run_ids_fall_back_to_the_active_run(self) -> None:
        xai = _runtime()
        manager = FeedbackManager(xai_runtime=xai)
        inside: list[FeedbackEvent] = []

        async def node(state: State) -> State:
            inside.append(await _submit(manager, ExecutionContext(run_id="langchain-run")))
            return {}

        with xai.collect_runs() as runs:
            await xai.instrument(_chain(("node", node))).ainvoke({})
        outside = await _submit(manager, ExecutionContext(run_id="langchain-run"))

        assert inside[0].provenance is not None
        assert inside[0].provenance.run_id == str(runs[0].run_id)
        assert outside.provenance is None


class TestCompletedRuns:
    async def test_feedback_after_a_run_resolves_from_the_store(self) -> None:
        xai = _runtime()
        manager = FeedbackManager(xai_runtime=xai)

        def agent(state: State) -> State:
            call = {"name": "echo", "args": {"text": "hi"}, "id": "call-9"}
            return {"messages": [AIMessage(content="", tool_calls=[call])]}

        graph = _chain(("agent", agent), ("tools", ToolNode([echo])))
        with xai.collect_runs() as runs:
            await xai.instrument(graph).ainvoke({"messages": []})
        run = runs[0]

        feedback = await _submit(
            manager,
            ExecutionContext(run_id=str(run.run_id), node_id="tools", tool_call_id="call-9"),
        )

        reference = feedback.provenance
        assert reference is not None
        assert reference.execution_id == str(run.execution.id)
        assert reference.node_execution_id == str(
            next(node.id for node in run.execution.nodes if node.node_id == "tools")
        )
        assert reference.tool_execution_id == str(run.execution.tools[0].id)
        assert (reference.decision_id, reference.evidence_ids) == (None, ())
        assert reference.metadata["status"] == "completed"
        assert reference.metadata["node_count"] == 2
        assert reference.metadata["continuation_of"] is None

    async def test_another_runs_feedback_from_inside_a_run_uses_that_run(self) -> None:
        xai = _runtime()
        manager = FeedbackManager(xai_runtime=xai)
        with xai.collect_runs() as earlier:
            await xai.instrument(_chain(("first", lambda state: {}))).ainvoke({})
        submitted: list[FeedbackEvent] = []

        async def node(state: State) -> State:
            context = ExecutionContext(run_id=str(earlier[0].run_id))
            submitted.append(await _submit(manager, context))
            return {}

        await xai.instrument(_chain(("second", node))).ainvoke({})

        reference = submitted[0].provenance
        assert reference is not None
        assert reference.run_id == str(earlier[0].run_id)
        assert reference.metadata["status"] == "completed"

    async def test_unknown_runs_and_missing_stores_have_no_provenance(self) -> None:
        xai = _runtime()
        manager = FeedbackManager(xai_runtime=xai)

        assert (await _submit(manager, ExecutionContext(run_id=str(uuid4())))).provenance is None
        xai.registry.remove(ProvenanceStore)
        assert (await _submit(manager, ExecutionContext(run_id=str(uuid4())))).provenance is None

    async def test_application_and_tenant_scope_the_lookup(self) -> None:
        xai = _runtime()
        store = xai.registry.get(ProvenanceStore)
        assert store is not None
        run_id = uuid4()
        await store.write(_execution(run_id, application_id="other", tenant_id="acme"))
        adapter = XAIProvenanceAdapter(xai)

        scoped = await adapter.resolve(
            _event(ExecutionContext(run_id=str(run_id), application_id="other", tenant_id="acme"))
        )
        defaults = await adapter.resolve(_event(ExecutionContext(run_id=str(run_id))))

        assert scoped is not None
        assert defaults is None

    async def test_the_latest_of_many_executions_wins(self) -> None:
        xai = _runtime()
        store = xai.registry.get(ProvenanceStore)
        assert store is not None
        run_id = uuid4()
        start = datetime(2026, 1, 1, tzinfo=UTC)
        executions = [_execution(run_id, at=start + timedelta(seconds=n)) for n in range(150)]
        for execution in executions:
            await store.write(execution)

        reference = await XAIProvenanceAdapter(xai).resolve(
            _event(ExecutionContext(run_id=str(run_id)))
        )

        assert reference is not None
        assert reference.execution_id == str(executions[-1].id)

    async def test_records_other_than_executions_are_ignored(self) -> None:
        class LooseStore(InMemoryProvenanceStore):
            async def query(self, query: StoreQuery) -> AsyncIterator[Any]:  # type: ignore[override]
                yield ProvenanceLink(
                    source_id="a", target_id="b", relation="derived_from", context=_context(uuid4())
                )
                async for item in super().query(query):
                    yield item

        xai = _runtime()
        store = LooseStore()
        xai.register(ProvenanceStore, store)
        run_id = uuid4()
        execution = _execution(run_id)
        await store.write(execution)

        reference = await XAIProvenanceAdapter(xai).resolve(
            _event(ExecutionContext(run_id=str(run_id)))
        )

        assert reference is not None
        assert reference.execution_id == str(execution.id)


class TestHumanInTheLoop:
    async def test_requests_and_resumed_runs_link_to_their_interactions(self) -> None:
        xai = _runtime()
        manager = FeedbackManager(xai_runtime=xai)
        bridge = HumanInTheLoopBridge(manager)

        def ask(state: State) -> State:
            return {"step": interrupt("approve the refund?")}

        graph = xai.instrument(_chain(("ask", ask), checkpointer=InMemorySaver()))
        config: Any = {"configurable": {"thread_id": "refund-1"}}
        with xai.collect_runs() as paused:
            (pending,) = extract_interrupts(await graph.ainvoke({}, config=config))
        snapshot = await graph.aget_state(config)

        request = await bridge.request(
            target=TARGET,
            interrupt=pending,
            execution_context=execution_context_from_snapshot(snapshot),
        )
        with xai.collect_runs() as resumed:
            await graph.ainvoke(bridge.resume_command("yes"), config=config)
        followup = await _submit(
            manager, ExecutionContext(run_id=str(resumed[0].run_id), interrupt_id=pending.id)
        )

        (interrupt_record,) = paused[0].execution.human_interactions
        (resume_record,) = resumed[0].execution.human_interactions
        assert request.provenance is not None
        assert request.provenance.human_interaction_id == str(interrupt_record.id)
        assert request.provenance.metadata["status"] == "interrupted"
        assert followup.provenance is not None
        assert followup.provenance.human_interaction_id == str(resume_record.id)
        assert followup.provenance.metadata["continuation_of"] == str(paused[0].run_id)


class TestFailures:
    async def test_provenance_failures_are_isolated_or_raised_by_policy(self) -> None:
        class BrokenStore(InMemoryProvenanceStore):
            async def query(self, query: StoreQuery) -> AsyncIterator[Any]:  # type: ignore[override]
                raise ConnectionError("provenance database down")
                yield  # pragma: no cover - makes this an async generator

        xai = _runtime()
        xai.register(ProvenanceStore, BrokenStore())
        sink = RecordingSink()
        context = ExecutionContext(run_id=str(uuid4()))

        isolated = await _submit(FeedbackManager(xai_runtime=xai, observability_sink=sink), context)
        strict = FeedbackManager(
            xai_runtime=xai,
            failure_policy=FailurePolicy(modes={FeedbackStage.PROVENANCE: FailureMode.BLOCKING}),
        )

        assert isolated.provenance is None
        assert sink.events[0].attributes["stage"] == "provenance"
        with pytest.raises(FeedbackCorrelationError, match="provenance"):
            await _submit(strict, context)


def _context(run_id: UUID, *, application_id: str = "app", tenant_id: str = "tenant") -> XaiContext:
    return XaiContext(
        application_id=application_id, tenant_id=tenant_id, graph_id="graph", run_id=run_id
    )


def _execution(
    run_id: UUID,
    *,
    application_id: str = "app",
    tenant_id: str = "tenant",
    at: datetime | None = None,
) -> Execution:
    started = at or datetime.now(UTC)
    return Execution(
        context=_context(run_id, application_id=application_id, tenant_id=tenant_id),
        status=ExecutionStatus.COMPLETED,
        started_at=started,
        timestamp=started,
    )


def _event(context: ExecutionContext) -> FeedbackEvent:
    return FeedbackEvent(
        source="human", category="rating", target=TARGET, execution_context=context
    )
