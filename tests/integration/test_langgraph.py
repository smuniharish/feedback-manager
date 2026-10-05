"""LangGraph integration: execution context, human-in-the-loop, and interrupts, with real graphs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Interrupt, interrupt
from langgraph_xai import RUN_ID_METADATA_KEY, XAIRuntime

from feedback_manager import (
    ExecutionContext,
    FeedbackLifecycleError,
    FeedbackManager,
    FeedbackNotFoundError,
    FeedbackSource,
    FeedbackStatus,
    FeedbackTarget,
    FeedbackTargetType,
    FeedbackValidationError,
)
from feedback_manager.integrations.langgraph import (
    INTERRUPT_KEY,
    HumanInTheLoopBridge,
    execution_context_from_config,
    execution_context_from_snapshot,
    extract_interrupts,
)

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

pytestmark = pytest.mark.integration

TARGET = FeedbackTarget(type=FeedbackTargetType.GRAPH, id="refunds")


class State(TypedDict, total=False):
    answer: str
    other: str


def _ask(state: State) -> State:
    return {"answer": interrupt({"question": "ask?"})}


def _check(state: State) -> State:
    return {"other": interrupt({"question": "check?"})}


def _approval_graph(*, nodes: tuple[str, ...] = ("ask",)) -> Any:
    builder = StateGraph(State)
    for name in nodes:
        builder.add_node(name, {"ask": _ask, "check": _check}[name])
        builder.add_edge(START, name)
        builder.add_edge(name, END)
    return builder.compile(checkpointer=InMemorySaver())


class TestExecutionContextFromConfig:
    def test_missing_config_gives_an_empty_context(self) -> None:
        assert execution_context_from_config(None) == ExecutionContext()

    def test_reads_configurable_and_metadata(self) -> None:
        context = execution_context_from_config(
            {
                "configurable": {"thread_id": "thread-1", "checkpoint_id": "cp-1"},
                "metadata": {
                    "xai_application_id": "app",
                    "xai_tenant_id": "tenant",
                    "xai_graph_id": "graph",
                    "langgraph_node": "plan",
                },
            },
            tool_call_id="call-1",
            generation_id="gen-1",
            message_id="msg-1",
            interrupt_id="int-1",
        )

        assert context == ExecutionContext(
            application_id="app",
            tenant_id="tenant",
            graph_id="graph",
            thread_id="thread-1",
            checkpoint_id="cp-1",
            node_id="plan",
            tool_call_id="call-1",
            generation_id="gen-1",
            message_id="msg-1",
            interrupt_id="int-1",
        )

    def test_run_id_precedence(self) -> None:
        xai_run, pinned, langchain_run = uuid4(), uuid4(), uuid4()
        metadata = {RUN_ID_METADATA_KEY: str(xai_run), "xai_run_id": str(pinned)}

        assert execution_context_from_config(
            {"metadata": metadata, "run_id": langchain_run}
        ).run_id == str(xai_run)
        assert execution_context_from_config(
            {"metadata": {"xai_run_id": pinned}, "run_id": langchain_run}
        ).run_id == str(pinned)
        assert execution_context_from_config({"run_id": langchain_run}).run_id == str(langchain_run)

    def test_identifiers_are_normalized(self) -> None:
        thread = uuid4()

        context = execution_context_from_config(
            {
                "configurable": {"thread_id": thread, "checkpoint_id": "   "},
                "metadata": {"langgraph_node": " plan ", "thread_id": "ignored"},
            },
            node_id="",
        )

        assert context.thread_id == str(thread)
        assert context.checkpoint_id is None
        assert context.node_id == "plan"

    def test_callback_metadata_supplies_the_thread(self) -> None:
        context = execution_context_from_config({"metadata": {"thread_id": 42}}, node_id="node")

        assert (context.thread_id, context.node_id) == ("42", "node")

    async def test_inside_an_instrumented_node_the_xai_run_id_is_used(self) -> None:
        xai = XAIRuntime(application_id="app", tenant_id="tenant", graph_id="graph")
        seen: list[ExecutionContext] = []

        def node(state: State, config: RunnableConfig) -> State:
            seen.append(execution_context_from_config(config))
            return {}

        builder = StateGraph(State)
        builder.add_node("node", node)
        builder.add_edge(START, "node")
        builder.add_edge("node", END)

        with xai.collect_runs() as runs:
            await xai.instrument(builder.compile()).ainvoke(
                {}, config={"configurable": {"thread_id": "t-1"}}
            )

        (context,) = seen
        assert context.run_id == str(runs[0].run_id)
        assert (context.thread_id, context.node_id) == ("t-1", "node")


class TestExecutionContextFromSnapshot:
    async def test_a_paused_graph_identifies_the_run_node_and_interrupt(self) -> None:
        xai = XAIRuntime(application_id="app", tenant_id="tenant", graph_id="graph")
        graph = xai.instrument(_approval_graph())
        config: Any = {"configurable": {"thread_id": "t-1"}}

        with xai.collect_runs() as runs:
            paused = await graph.ainvoke({}, config=config)
        (pending,) = extract_interrupts(paused)
        context = execution_context_from_snapshot(await graph.aget_state(config))

        assert context.run_id == str(runs[0].run_id)
        assert context.thread_id == "t-1"
        assert context.checkpoint_id is not None
        assert (context.node_id, context.interrupt_id) == ("ask", pending.id)

    async def test_without_a_single_pending_interrupt_no_interrupt_is_chosen(self) -> None:
        both = _approval_graph(nodes=("ask", "check"))
        config: Any = {"configurable": {"thread_id": "t-2"}}
        assert len(extract_interrupts(await both.ainvoke({}, config=config))) == 2

        context = execution_context_from_snapshot(await both.aget_state(config))

        assert (context.node_id, context.interrupt_id) == (None, None)
        assert context.thread_id == "t-2"


class TestHumanInTheLoopBridge:
    async def test_approval_round_trip(self) -> None:
        manager = FeedbackManager()
        bridge = HumanInTheLoopBridge(manager)
        graph = _approval_graph()
        config: Any = {"configurable": {"thread_id": "t-1"}}
        (pending,) = extract_interrupts(await graph.ainvoke({}, config=config))
        snapshot = await graph.aget_state(config)

        request = await bridge.request(
            target=TARGET,
            interrupt=pending,
            execution_context=execution_context_from_snapshot(snapshot),
            metadata={"queue": "refunds"},
        )
        resolved = await bridge.resolve(request.feedback_id, response="approve", approved=True)
        result = await graph.ainvoke(bridge.resume_command("approve"), config=config)

        assert request.source == FeedbackSource.SYSTEM
        assert request.payload == {"prompt": {"question": "ask?"}}
        assert request.execution_context is not None
        assert request.execution_context.interrupt_id == pending.id
        assert resolved.status is FeedbackStatus.RESOLVED
        assert resolved.resolution == {"response": "approve", "approved": True}
        assert result == {"answer": "approve"}
        assert await bridge.resolve(request.feedback_id, response="approve") == resolved

    async def test_rejection_and_conflicting_decisions(self) -> None:
        manager = FeedbackManager()
        bridge = HumanInTheLoopBridge(manager)
        request = await bridge.request(target=TARGET, prompt="ship it?")

        rejected = await bridge.resolve(
            request.feedback_id, response={"why": "risky"}, approved=False
        )

        assert rejected.status is FeedbackStatus.REJECTED
        assert rejected.resolution == {"response": {"why": "risky"}, "approved": False}
        with pytest.raises(FeedbackLifecycleError):
            await bridge.resolve(request.feedback_id, response="ok", approved=True)

    @pytest.mark.parametrize("advanced_to", ["acknowledge", "mark_handled"])
    async def test_resolve_continues_from_wherever_the_request_is(self, advanced_to: str) -> None:
        manager = FeedbackManager()
        bridge = HumanInTheLoopBridge(manager)
        request = await bridge.request(target=TARGET, prompt="ok?")
        await manager.acknowledge(request.feedback_id)
        if advanced_to == "mark_handled":
            await manager.mark_handled(request.feedback_id)

        resolved = await bridge.resolve(request.feedback_id, response="yes")

        assert resolved.status is FeedbackStatus.RESOLVED
        assert resolved.resolution == {"response": "yes", "approved": None}

    async def test_request_validation(self) -> None:
        bridge = HumanInTheLoopBridge(FeedbackManager())
        pending = Interrupt(value="approve?", id="interrupt-1")

        with pytest.raises(FeedbackValidationError, match="interrupt or a prompt"):
            await bridge.request(target=TARGET)
        with pytest.raises(FeedbackValidationError, match="does not match"):
            await bridge.request(
                target=TARGET,
                interrupt=pending,
                execution_context=ExecutionContext(interrupt_id="interrupt-2"),
            )
        with pytest.raises(FeedbackNotFoundError):
            await bridge.resolve(uuid4(), response="yes")

    async def test_prompts_become_json(self) -> None:
        bridge = HumanInTheLoopBridge(FeedbackManager())
        pending = Interrupt(value="ignored", id="interrupt-1")
        marker = object()

        overridden = await bridge.request(
            target=TARGET,
            interrupt=pending,
            prompt={"id": UUID(int=1), "tags": {"a"}},
            execution_context=ExecutionContext(interrupt_id="interrupt-1"),
            idempotency_key="approval-1",
        )
        unserializable = await bridge.request(target=TARGET, prompt=marker)

        assert overridden.payload == {
            "prompt": {"id": "00000000-0000-0000-0000-000000000001", "tags": ["a"]}
        }
        assert overridden.idempotency_key == "approval-1"
        assert unserializable.payload == {"prompt": str(marker)}

    async def test_resume_command_can_target_one_interrupt(self) -> None:
        graph = _approval_graph(nodes=("ask", "check"))
        config: Any = {"configurable": {"thread_id": "t-3"}}
        pending = extract_interrupts(await graph.ainvoke({}, config=config))
        check = next(item for item in pending if item.value == {"question": "check?"})

        partial_result = await graph.ainvoke(
            HumanInTheLoopBridge.resume_command("checked", interrupt_id=check.id), config=config
        )

        assert partial_result.get("other") == "checked"
        assert [item.value for item in extract_interrupts(partial_result)] == [{"question": "ask?"}]
        assert HumanInTheLoopBridge.resume_command("yes").resume == "yes"


class TestExtractInterrupts:
    def test_plain_results_have_no_interrupts(self) -> None:
        assert extract_interrupts({"answer": "done"}) == ()

    def test_only_interrupt_objects_are_returned(self) -> None:
        pending = Interrupt(value="approve?", id="interrupt-1")

        assert extract_interrupts({INTERRUPT_KEY: [pending, "noise"]}) == (pending,)

    async def test_stream_chunks(self) -> None:
        graph = _approval_graph()
        config: Any = {"configurable": {"thread_id": "t-4"}}

        found = [
            interrupt_
            async for chunk in graph.astream({}, config=config, stream_mode="updates")
            for interrupt_ in extract_interrupts(chunk)
        ]

        assert [item.value for item in found] == [{"question": "ask?"}]
