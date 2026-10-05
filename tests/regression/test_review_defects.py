"""Regression tests: one per defect found in the 0.1.1 review.

Each test reproduces the scenario that failed in 0.1.0 and asserts the fixed
behavior, so the defect cannot return unnoticed.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, TypedDict

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt
from langgraph_xai import XAIRuntime

from feedback_manager import (
    ExecutionContext,
    FeedbackEvent,
    FeedbackLifecycleError,
    FeedbackManager,
    FeedbackManagerError,
    FeedbackQuery,
    FeedbackStatus,
    FeedbackTarget,
    FeedbackValidationError,
)
from feedback_manager.contracts import FeedbackHandler, FeedbackHandlerResult
from feedback_manager.integrations.langchain import FeedbackCallbackHandler, capture_tool_feedback
from feedback_manager.integrations.langgraph import (
    HumanInTheLoopBridge,
    execution_context_from_config,
)
from feedback_manager.policies import FailureMode, FailurePolicy, FeedbackStage, RetentionPolicy
from feedback_manager.routing import DefaultFeedbackRouter
from feedback_manager.storage import InMemoryFeedbackStore

if TYPE_CHECKING:
    from tests.conftest import RecordingSink

TARGET = FeedbackTarget(type="generation", id="gen-1")


async def _submit(manager: FeedbackManager, **fields: Any) -> FeedbackEvent:
    return await manager.submit(source="human", category="correction", target=TARGET, **fields)


async def test_illegal_closing_moves_do_not_persist_a_resolution() -> None:
    manager = FeedbackManager()
    event = await _submit(manager)

    with pytest.raises(FeedbackLifecycleError):
        await manager.resolve(event.feedback_id, resolution={"applied": True})

    stored = await manager.get(event.feedback_id)
    assert stored is not None
    assert stored.resolution is None


async def test_repeated_moves_neither_republish_nor_overwrite(sink: RecordingSink) -> None:
    manager = FeedbackManager(observability_sink=sink)
    event = await _submit(manager)
    await manager.reject(event.feedback_id, reason="first")

    again = await manager.reject(event.feedback_id, reason="second")

    assert again.resolution == {"reason": "first"}
    assert sink.names == ["feedback.received", "feedback.rejected"]


async def test_each_terminal_status_has_its_own_event_name(sink: RecordingSink) -> None:
    manager = FeedbackManager(observability_sink=sink)
    for close in (manager.reject, manager.cancel, manager.expire):
        await close((await _submit(manager)).feedback_id)

    assert [name for name in sink.names if name != "feedback.received"] == [
        "feedback.rejected",
        "feedback.cancelled",
        "feedback.expired",
    ]


async def test_failures_surface_as_typed_package_errors() -> None:
    class Broken(FeedbackHandler):
        async def handle(self, feedback: FeedbackEvent) -> FeedbackHandlerResult:
            raise RuntimeError("boom")

    with pytest.raises(FeedbackManagerError):
        FeedbackManager(store="not a store")  # type: ignore[arg-type]
    with pytest.raises(FeedbackManagerError):
        await _submit(FeedbackManager(), payload="not a mapping")
    blocking = FeedbackManager(
        router=DefaultFeedbackRouter(default_handlers=(Broken(),)),
        failure_policy=FailurePolicy(modes={FeedbackStage.HANDLER: FailureMode.BLOCKING}),
    )
    with pytest.raises(FeedbackManagerError):
        await _submit(blocking)


def test_overriding_one_failure_mode_keeps_the_others() -> None:
    policy = FailurePolicy(modes={FeedbackStage.HANDLER: FailureMode.BLOCKING})

    assert policy.mode_for(FeedbackStage.ROUTING) is FailureMode.BEST_EFFORT
    assert len(policy.modes) == len(FeedbackStage)


async def test_stage_failures_emit_feedback_failed(sink: RecordingSink) -> None:
    class Broken(FeedbackHandler):
        async def handle(self, feedback: FeedbackEvent) -> FeedbackHandlerResult:
            raise RuntimeError("boom")

    manager = FeedbackManager(
        router=DefaultFeedbackRouter(default_handlers=(Broken(),)), observability_sink=sink
    )

    await _submit(manager)

    assert "feedback.failed" in sink.names


async def test_streams_receive_events_published_before_the_first_read() -> None:
    manager = FeedbackManager()
    stream = manager.stream()

    event = await _submit(manager)

    assert await asyncio.wait_for(anext(stream), timeout=5) == event


def test_negative_limits_are_rejected_instead_of_dropping_results() -> None:
    with pytest.raises(FeedbackValidationError):
        FeedbackQuery(limit=-1)


def test_config_extraction_handles_uuids_and_langgraph_xai_metadata() -> None:
    thread, run = uuid.uuid4(), uuid.uuid4()

    context = execution_context_from_config(
        {
            "configurable": {"thread_id": thread},
            "metadata": {"langgraph_node": "plan", "langgraph_xai_run_id": str(run)},
        }
    )

    assert (context.thread_id, context.run_id, context.node_id) == (str(thread), str(run), "plan")


class _State(TypedDict, total=False):
    messages: list[Any]
    answer: str


@tool
async def _failing(x: int) -> int:
    """Always fails."""
    raise ValueError("tool exploded")


async def test_callback_feedback_is_recorded_once_with_context_and_ignores_interrupts() -> None:
    def call(state: _State) -> _State:
        tool_call = {"name": "_failing", "args": {"x": 1}, "id": "call-1"}
        return {"messages": [AIMessage(content="", tool_calls=[tool_call])]}

    def ask(state: _State) -> _State:
        return {"answer": interrupt("approve?")}

    tools = StateGraph(_State)
    tools.add_node("agent", call)
    tools.add_node("tools", ToolNode([_failing], handle_tool_errors=False))
    tools.add_edge(START, "agent")
    tools.add_edge("agent", "tools")
    tools.add_edge("tools", END)
    pause = StateGraph(_State)
    pause.add_node("ask", ask)
    pause.add_edge(START, "ask")
    pause.add_edge("ask", END)
    manager = FeedbackManager()
    config: Any = {
        "callbacks": [FeedbackCallbackHandler(manager)],
        "configurable": {"thread_id": "t-1"},
    }

    with pytest.raises(ValueError, match="tool exploded"):
        await tools.compile().ainvoke({"messages": []}, config=config)
    await pause.compile(checkpointer=InMemorySaver()).ainvoke({}, config=config)

    (event,) = await manager.query()
    assert event.execution_context is not None
    assert event.execution_context.thread_id == "t-1"


async def test_tool_capture_ignores_process_exits_and_never_masks_tool_errors() -> None:
    class DownStore(InMemoryFeedbackStore):
        async def create(self, feedback: FeedbackEvent) -> FeedbackEvent:
            raise ConnectionError("db down")

    manager = FeedbackManager()
    with pytest.raises(KeyboardInterrupt):
        async with capture_tool_feedback(manager, tool_call_id="c-1"):
            raise KeyboardInterrupt
    assert await manager.query() == []

    with pytest.raises(TimeoutError):
        async with capture_tool_feedback(FeedbackManager(store=DownStore()), tool_call_id="c-2"):
            raise TimeoutError("tool timed out")


async def test_resolving_an_advanced_request_succeeds() -> None:
    manager = FeedbackManager()
    bridge = HumanInTheLoopBridge(manager)
    request = await bridge.request(target=TARGET, prompt="ok?")
    await manager.acknowledge(request.feedback_id)
    await manager.mark_handled(request.feedback_id)

    resolved = await bridge.resolve(request.feedback_id, response="yes", approved=True)

    assert resolved.status is FeedbackStatus.RESOLVED


async def test_post_run_provenance_finds_the_run_by_its_run_id() -> None:
    xai = XAIRuntime(application_id="app", tenant_id="tenant", graph_id="graph")
    graph = StateGraph(_State)
    graph.add_node("node", lambda state: {})
    graph.add_edge(START, "node")
    graph.add_edge("node", END)
    with xai.collect_runs() as runs:
        await xai.instrument(graph.compile()).ainvoke({})

    event = await _submit(
        FeedbackManager(xai_runtime=xai),
        execution_context=ExecutionContext(run_id=str(runs[0].run_id)),
    )

    assert event.provenance is not None
    assert event.provenance.execution_id == str(runs[0].execution.id)


def test_disabled_log_levels_are_not_rendered(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from feedback_manager._logging import get_logger

    calls: list[None] = []
    monkeypatch.setattr("feedback_manager._logging._RENDERER", lambda *args: calls.append(None))

    with caplog.at_level(logging.WARNING):
        get_logger("feedback_manager.regression").info("ignored")

    assert calls == []


async def test_empty_identifiers_are_rejected() -> None:
    with pytest.raises(FeedbackValidationError):
        await FeedbackManager().submit(source="", category="", target=TARGET)


async def test_a_failing_observability_sink_does_not_fail_stored_feedback() -> None:
    class BrokenSink:
        def emit(self, event: object) -> None:
            raise ConnectionError("collector down")

    manager = FeedbackManager(observability_sink=BrokenSink())

    event = await _submit(manager)

    assert await manager.get(event.feedback_id) == event


def test_handled_feedback_is_not_considered_expired() -> None:
    now = datetime.now(UTC)
    handled = FeedbackEvent(
        source="human",
        category="rating",
        target=TARGET,
        status=FeedbackStatus.HANDLED,
        created_at=now - timedelta(days=30),
    )

    assert RetentionPolicy(max_pending_age=timedelta(days=1)).is_expired(handled, now=now) is False


async def test_non_finite_numbers_are_rejected() -> None:
    with pytest.raises(FeedbackValidationError):
        await _submit(FeedbackManager(), payload={"score": float("inf")})
