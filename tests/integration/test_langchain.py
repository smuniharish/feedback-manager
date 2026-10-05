"""LangChain integration: callback capture and `capture_tool_feedback`, with real runnables."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, TypedDict
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphInterrupt, NodeCancelledError, NodeTimeoutError
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from feedback_manager import (
    ExecutionContext,
    FeedbackCategory,
    FeedbackEvent,
    FeedbackManager,
    FeedbackSource,
    FeedbackStoreError,
    FeedbackTargetType,
    FeedbackValidationError,
)
from feedback_manager.integrations.langchain import (
    FeedbackCallbackHandler,
    callbacks,
    capture_tool_feedback,
    category_for_error,
)
from feedback_manager.integrations.langchain.failures import caused_by, is_reportable
from feedback_manager.storage import InMemoryFeedbackStore
from tests.integration.doubles import FakeChatModel, FakeLLM, FakeRetriever

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration


class State(TypedDict, total=False):
    messages: list[Any]
    answer: str


@tool
async def lookup(city: str) -> str:
    """Look up a city (always fails)."""
    raise ValueError(f"no data for {city}")


@tool
async def echo(text: str) -> str:
    """Echo the text."""
    return text


def _tool_graph(*, wrap: str | None = None) -> Any:
    def agent(state: State) -> State:
        return {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "lookup", "args": {"city": "Oslo"}, "id": "call-7"}],
                )
            ]
        }

    tools = ToolNode([lookup], handle_tool_errors=False)

    async def call_tools(state: State) -> State:
        try:
            return await tools.ainvoke(state)
        except ValueError as error:
            if wrap == "cause":
                raise RuntimeError("tools failed") from error
            raise RuntimeError("tools failed") from None

    builder = StateGraph(State)
    builder.add_node("agent", agent)
    builder.add_node("tools", tools if wrap is None else call_tools)
    builder.add_edge(START, "agent")
    builder.add_edge("agent", "tools")
    builder.add_edge("tools", END)
    return builder.compile()


async def _events(manager: FeedbackManager) -> list[FeedbackEvent]:
    return list(await manager.query())


class TestCallbackHandler:
    async def test_a_tool_failure_in_a_graph_is_recorded_once_with_context(self) -> None:
        manager = FeedbackManager()
        handler = FeedbackCallbackHandler(manager)

        with pytest.raises(ValueError, match="no data"):
            await _tool_graph().ainvoke(
                {"messages": []},
                config={"callbacks": [handler], "configurable": {"thread_id": "thread-1"}},
            )

        (event,) = await _events(manager)
        assert event.source == FeedbackSource.TOOL
        assert event.category == FeedbackCategory.FAILURE
        assert event.feedback_type == "tool_error"
        assert (event.target.type, event.target.id) == (FeedbackTargetType.TOOL_CALL, "call-7")
        assert event.payload == {
            "error": "no data for Oslo",
            "error_type": "ValueError",
            "operation": "lookup",
        }
        context = event.execution_context
        assert context is not None
        assert (context.thread_id, context.node_id, context.tool_call_id) == (
            "thread-1",
            "tools",
            "call-7",
        )
        assert event.correlation_id == "thread-1"
        assert handler._runs == {}
        assert handler._reported == {}

    @pytest.mark.parametrize("wrap", ["cause", "context"])
    async def test_wrapped_failures_are_not_recorded_again(self, wrap: str) -> None:
        manager = FeedbackManager()

        with pytest.raises(RuntimeError, match="tools failed"):
            await _tool_graph(wrap=wrap).ainvoke(
                {"messages": []}, config={"callbacks": [FeedbackCallbackHandler(manager)]}
            )

        assert [event.feedback_type for event in await _events(manager)] == ["tool_error"]

    async def test_a_failing_node_is_recorded_as_agent_feedback_about_the_node(self) -> None:
        def broken(state: State) -> State:
            raise KeyError("missing field")

        builder = StateGraph(State)
        builder.add_node("plan", broken)
        builder.add_edge(START, "plan")
        builder.add_edge("plan", END)
        manager = FeedbackManager()

        with pytest.raises(KeyError):
            await builder.compile().ainvoke(
                {}, config={"callbacks": [FeedbackCallbackHandler(manager)]}
            )

        (event,) = await _events(manager)
        assert event.source == FeedbackSource.AGENT
        assert event.feedback_type == "chain_error"
        assert (event.target.type, event.target.id) == (FeedbackTargetType.NODE, "plan")

    async def test_interrupts_are_control_flow_not_failures(self) -> None:
        def ask(state: State) -> State:
            return {"answer": interrupt("approve?")}

        builder = StateGraph(State)
        builder.add_node("ask", ask)
        builder.add_edge(START, "ask")
        builder.add_edge("ask", END)
        manager = FeedbackManager()

        await builder.compile(checkpointer=InMemorySaver()).ainvoke(
            {},
            config={
                "callbacks": [FeedbackCallbackHandler(manager)],
                "configurable": {"thread_id": "t"},
            },
        )

        assert await _events(manager) == []

    async def test_chat_model_failures_are_generation_feedback(self) -> None:
        manager = FeedbackManager()
        model = FakeChatModel(error=TimeoutError("model timed out"))

        with pytest.raises(TimeoutError):
            await model.ainvoke("hello", config={"callbacks": [FeedbackCallbackHandler(manager)]})

        (event,) = await _events(manager)
        assert event.source == FeedbackSource.GENERATION
        assert event.category == FeedbackCategory.TIMEOUT
        assert event.feedback_type == "model_error"
        assert event.target.type == FeedbackTargetType.GENERATION
        assert event.execution_context is not None
        assert event.execution_context.generation_id == event.target.id

    async def test_completion_model_and_retriever_failures(self) -> None:
        manager = FeedbackManager()
        handler = FeedbackCallbackHandler(manager)

        with pytest.raises(ConnectionError):
            await FakeLLM(error=ConnectionError("down")).ainvoke(
                "hi", config={"callbacks": [handler]}
            )
        with pytest.raises(LookupError):
            await FakeRetriever(error=LookupError("index missing")).ainvoke(
                "query", config={"callbacks": [handler]}
            )

        model_event, retriever_event = await _events(manager)
        assert model_event.feedback_type == "model_error"
        assert retriever_event.source == FeedbackSource.TOOL
        assert retriever_event.feedback_type == "retriever_error"
        assert retriever_event.target.type == FeedbackTargetType.RUN

    async def test_successful_runs_record_nothing_and_leave_no_state(self) -> None:
        manager = FeedbackManager()
        handler = FeedbackCallbackHandler(manager)
        config: Any = {"callbacks": [handler]}

        await echo.ainvoke({"text": "hi"}, config=config)
        await FakeChatModel().ainvoke("hi", config=config)
        await FakeLLM().ainvoke("hi", config=config)
        await FakeRetriever().ainvoke("hi", config=config)
        await (FakeChatModel() | (lambda message: message.content) | FakeChatModel()).ainvoke(
            "hi", config=config
        )

        assert await _events(manager) == []
        assert handler._runs == {}
        assert handler._reported == {}

    async def test_failures_without_a_recorded_start_are_still_recorded(self) -> None:
        manager = FeedbackManager()
        handler = FeedbackCallbackHandler(manager)
        root = uuid4()
        await handler.on_chain_start({"name": "pipeline"}, {}, run_id=root)

        await handler.on_tool_error(ValueError("a"), run_id=uuid4(), parent_run_id=root)
        await handler.on_tool_error(ValueError("b"), run_id=uuid4(), parent_run_id=uuid4())
        await handler.on_chain_error(ValueError("c"), run_id=root)

        events = await _events(manager)
        assert [event.payload["error"] for event in events] == ["a", "b", "c"]
        assert events[2].payload["operation"] == "pipeline"
        assert events[2].target.type == FeedbackTargetType.RUN
        assert handler._reported == {}

    async def test_tool_call_ids_fall_back_to_the_start_event_then_the_run(self) -> None:
        manager = FeedbackManager()
        handler = FeedbackCallbackHandler(manager)
        started, bare = uuid4(), uuid4()
        await handler.on_tool_start(
            {}, "input", run_id=started, name="search", tool_call_id="call-from-start"
        )

        await handler.on_tool_error(ValueError("x"), run_id=started)
        await handler.on_tool_error(ValueError("y"), run_id=bare)

        first, second = await _events(manager)
        assert first.target.id == "call-from-start"
        assert first.payload["operation"] == "search"
        assert second.target.id == str(bare)
        assert second.payload["operation"] is None

    async def test_non_failures_are_ignored(self) -> None:
        manager = FeedbackManager()
        handler = FeedbackCallbackHandler(manager)

        await handler.on_chain_error(GraphInterrupt(()), run_id=uuid4())
        await handler.on_chain_error(KeyboardInterrupt(), run_id=uuid4())

        assert await _events(manager) == []
        assert handler._reported == {}


async def _hang(*_: Any) -> None:
    await asyncio.Event().wait()


class HangingChatModel(FakeChatModel):
    """A chat model whose calls never finish, streamed or not."""

    async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
        await _hang()

    async def _astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        yield ChatGenerationChunk(message=AIMessageChunk(content="partial"))
        await _hang()


@tool
async def wait_forever(city: str) -> str:
    """Look up a city (never answers)."""
    await _hang()
    return city


def _graph(**nodes: Any) -> Any:
    """A graph that runs ``nodes`` in parallel; each value is a body or ``(body, options)``."""
    builder = StateGraph(State)
    for name, node in nodes.items():
        body, options = node if isinstance(node, tuple) else (node, {})
        builder.add_node(name, body, **options)
        builder.add_edge(START, name)
        builder.add_edge(name, END)
    return builder.compile()


class TestCancellations:
    async def test_a_cancelled_run_is_recorded_once_and_leaves_no_state(self) -> None:
        def plan(state: State) -> State:
            call = {"name": "wait_forever", "args": {"city": "Oslo"}, "id": "call-1"}
            return {"messages": [AIMessage(content="", tool_calls=[call])]}

        async def think(state: State) -> State:
            await HangingChatModel().ainvoke("hi")
            return {}

        builder = StateGraph(State)
        builder.add_node("plan", plan)
        builder.add_node("tools", ToolNode([wait_forever], handle_tool_errors=False))
        builder.add_node("think", think)
        builder.add_edge(START, "plan")
        builder.add_edge("plan", "tools")
        builder.add_edge("plan", "think")
        builder.add_edge("tools", END)
        builder.add_edge("think", END)
        graph = builder.compile()
        manager = FeedbackManager()
        handler = FeedbackCallbackHandler(manager)

        for _ in range(3):
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.2):
                    await graph.ainvoke({"messages": []}, {"callbacks": [handler]})

        events = await _events(manager)
        assert [(event.category, event.target.type) for event in events] == [
            (FeedbackCategory.CANCELLATION, FeedbackTargetType.RUN)
        ] * 3
        assert (handler._runs, handler._trees, handler._reported) == ({}, {}, {})

    async def test_a_node_failure_is_recorded_without_the_cancellations_it_caused(self) -> None:
        async def broken(state: State) -> State:
            await asyncio.sleep(0.05)
            raise ValueError("lookup failed")

        manager = FeedbackManager()

        with pytest.raises(ValueError, match="lookup failed"):
            await _graph(broken=broken, slow=_hang).ainvoke(
                {}, {"callbacks": [FeedbackCallbackHandler(manager)]}
            )

        (event,) = await _events(manager)
        assert (event.category, event.target.type, event.target.id) == (
            FeedbackCategory.FAILURE,
            FeedbackTargetType.NODE,
            "broken",
        )

    @pytest.mark.parametrize(
        ("node", "error", "category"),
        [
            ((_hang, {"timeout": 0.1}), NodeTimeoutError, FeedbackCategory.TIMEOUT),
            (lambda state: _raise(asyncio.CancelledError()), NodeCancelledError, None),
        ],
        ids=["node timeout", "node cancels itself"],
    )
    async def test_langgraph_node_errors_are_recorded_about_the_node(
        self, node: Any, error: type[Exception], category: FeedbackCategory | None
    ) -> None:
        manager = FeedbackManager()

        with pytest.raises(error):
            await _graph(work=node).ainvoke({}, {"callbacks": [FeedbackCallbackHandler(manager)]})

        (event,) = await _events(manager)
        assert event.category == (category or FeedbackCategory.CANCELLATION)
        assert (event.target.type, event.target.id) == (FeedbackTargetType.NODE, "work")
        assert event.payload["error_type"] == error.__name__
        assert event.payload["operation"] == "work"
        assert event.execution_context is not None
        assert event.execution_context.node_id == "work"

    async def test_a_timeout_inside_a_node_is_a_timeout_not_a_cancellation(self) -> None:
        async def answer(state: State) -> State:
            async with asyncio.timeout(0.1):
                async for _ in HangingChatModel().astream("hi"):
                    pass
            return {}

        manager = FeedbackManager()

        with pytest.raises(TimeoutError):
            await _graph(answer=answer).ainvoke(
                {}, {"callbacks": [FeedbackCallbackHandler(manager)]}
            )

        (event,) = await _events(manager)
        assert (event.category, event.target.type, event.target.id) == (
            FeedbackCategory.TIMEOUT,
            FeedbackTargetType.NODE,
            "answer",
        )

    async def test_a_cancelled_top_level_model_stream_is_recorded(self) -> None:
        manager = FeedbackManager()

        async def stream() -> None:
            async for _ in HangingChatModel().astream(
                "hi", config={"callbacks": [FeedbackCallbackHandler(manager)]}
            ):
                pass

        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.1):
                await stream()

        (event,) = await _events(manager)
        assert (event.source, event.category) == (
            FeedbackSource.GENERATION,
            FeedbackCategory.CANCELLATION,
        )

    async def test_callbacks_for_runs_already_forgotten_are_ignored(self) -> None:
        manager = FeedbackManager()
        handler = FeedbackCallbackHandler(manager)

        # LangGraph can report a cancelled node after the graph run has ended.
        await handler.on_chain_end({}, run_id=uuid4(), parent_run_id=uuid4())
        await handler.on_chain_error(
            asyncio.CancelledError(), run_id=uuid4(), parent_run_id=uuid4()
        )

        assert await _events(manager) == []
        assert (handler._runs, handler._trees, handler._reported) == ({}, {}, {})

    async def test_cancelled_top_level_calls_are_forgotten_beyond_the_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(callbacks, "_MAX_TREES", 2)
        manager = FeedbackManager()
        handler = FeedbackCallbackHandler(manager)

        for _ in range(3):
            # LangChain reports no end for a cancelled top-level tool call.
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.05):
                    await wait_forever.ainvoke({"city": "Oslo"}, {"callbacks": [handler]})

        assert len(handler._trees) == len(handler._runs) == 2
        assert await _events(manager) == []


def _raise(error: BaseException) -> State:
    raise error


class TestFailureClassification:
    @pytest.mark.parametrize(
        ("error", "category"),
        [
            (asyncio.CancelledError(), FeedbackCategory.CANCELLATION),
            (NodeCancelledError("plan"), FeedbackCategory.CANCELLATION),
            (TimeoutError(), FeedbackCategory.TIMEOUT),
            (NodeTimeoutError("plan", 2.0, kind="run", run_timeout=1.0), FeedbackCategory.TIMEOUT),
            (ValueError(), FeedbackCategory.FAILURE),
        ],
    )
    def test_categories(self, error: BaseException, category: FeedbackCategory) -> None:
        assert category_for_error(error) == category

    @pytest.mark.parametrize(
        ("error", "reportable"),
        [
            (ValueError(), True),
            (asyncio.CancelledError(), True),
            (GraphInterrupt(()), False),
            (KeyboardInterrupt(), False),
            (SystemExit(), False),
        ],
    )
    def test_reportable(self, error: BaseException, reportable: bool) -> None:
        assert is_reportable(error) is reportable

    def test_caused_by_follows_causes_contexts_and_groups(self) -> None:
        origin = ValueError("origin")
        cause = RuntimeError("cause")
        cause.__cause__ = origin
        context = RuntimeError("context")
        context.__context__ = origin
        group = ExceptionGroup("group", [TypeError("other"), origin])
        cycle = RuntimeError("cycle")
        cycle.__context__ = cycle

        assert caused_by(origin, origin)
        assert caused_by(cause, origin)
        assert caused_by(context, origin)
        assert caused_by(group, origin)
        assert not caused_by(cycle, origin)
        assert not caused_by(TypeError("unrelated"), origin)


class TestCaptureToolFeedback:
    async def test_failures_are_recorded_and_re_raised(self) -> None:
        manager = FeedbackManager()

        with pytest.raises(TimeoutError, match="slow"):
            async with capture_tool_feedback(
                manager,
                tool_call_id="call-1",
                tool_name="search",
                execution_context=ExecutionContext(thread_id="thread-1", tool_call_id="stale"),
            ):
                raise TimeoutError("slow")

        (event,) = await _events(manager)
        assert event.category == FeedbackCategory.TIMEOUT
        assert event.target.id == "call-1"
        assert event.payload["operation"] == "search"
        assert event.execution_context is not None
        assert event.execution_context.tool_call_id == "call-1"
        assert event.execution_context.thread_id == "thread-1"

    async def test_success_and_control_flow_record_nothing(self) -> None:
        manager = FeedbackManager()

        async with capture_tool_feedback(manager, tool_call_id="call-1"):
            pass
        with pytest.raises(GraphInterrupt):
            async with capture_tool_feedback(manager, tool_call_id="call-2"):
                raise GraphInterrupt(())

        assert await _events(manager) == []

    async def test_cancellation_passes_through_unrecorded(self) -> None:
        manager = FeedbackManager()

        with pytest.raises(asyncio.CancelledError):
            async with capture_tool_feedback(manager, tool_call_id="call-1"):
                raise asyncio.CancelledError

        assert await _events(manager) == []

    async def test_invalid_tool_call_ids_fail_before_the_tool_runs(self) -> None:
        ran = False
        with pytest.raises(FeedbackValidationError):
            async with capture_tool_feedback(FeedbackManager(), tool_call_id=""):
                ran = True
        assert not ran

    async def test_a_feedback_failure_never_masks_the_tool_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        class DownStore(InMemoryFeedbackStore):
            async def create(self, feedback: FeedbackEvent) -> FeedbackEvent:
                raise ConnectionError("db down")

        manager = FeedbackManager(store=DownStore())

        with (
            caplog.at_level(logging.WARNING, logger="feedback_manager.integrations.langchain"),
            pytest.raises(ValueError, match="bad input") as raised,
        ):
            async with capture_tool_feedback(manager, tool_call_id="call-1"):
                raise ValueError("bad input")

        assert any(FeedbackStoreError.__name__ in note for note in raised.value.__notes__)
        assert "tool failure not recorded" in caplog.text
