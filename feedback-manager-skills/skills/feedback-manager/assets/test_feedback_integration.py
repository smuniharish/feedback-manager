"""A pytest template for testing an application's feedback-manager integration.

Copy this file into your test suite, then replace the graphs, handlers, and
manager construction marked "Replace" with your own. It needs pytest and
pytest-asyncio, and runs offline:

    pytest test_feedback_integration.py
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from feedback_manager import (
    FeedbackEvent,
    FeedbackHandlerError,
    FeedbackLifecycleError,
    FeedbackManager,
    FeedbackQuery,
    FeedbackStatus,
    FeedbackTarget,
)
from feedback_manager.contracts import FeedbackHandler, FeedbackHandlerResult
from feedback_manager.integrations.langchain import FeedbackCallbackHandler
from feedback_manager.integrations.langgraph import (
    HumanInTheLoopBridge,
    execution_context_from_snapshot,
    extract_interrupts,
)
from feedback_manager.policies import FailureMode, FailurePolicy, FeedbackStage
from feedback_manager.routing import DefaultFeedbackRouter, RoutingRule, by_category

if TYPE_CHECKING:
    from feedback_manager.observability import ObservabilityEvent

pytestmark = pytest.mark.asyncio

# Every stage raises in tests, so a broken handler or subscriber fails the test.
STRICT = FailurePolicy(modes=dict.fromkeys(FeedbackStage, FailureMode.BLOCKING))


class RecordingSink:
    """Keeps the name of every observability event, for assertions."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def emit(self, event: ObservabilityEvent) -> None:
        self.names.append(event.name)


@pytest.fixture
def sink() -> RecordingSink:
    return RecordingSink()


@pytest.fixture
def manager(sink: RecordingSink) -> FeedbackManager:
    # Replace with how your application builds its manager; keep STRICT.
    return FeedbackManager(failure_policy=STRICT, observability_sink=sink)


def correction(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "source": "human",
        "category": "correction",
        "target": FeedbackTarget(type="generation", id="gen-1"),
        "payload": {"corrected_text": "Canberra is the capital of Australia."},
        "idempotency_key": "correction:gen-1:user-7",
    }
    fields.update(overrides)
    return fields


async def test_feedback_is_stored_once_and_resolved(
    manager: FeedbackManager, sink: RecordingSink
) -> None:
    feedback = await manager.submit(**correction())
    retried = await manager.submit(**correction())
    assert retried.feedback_id == feedback.feedback_id

    with pytest.raises(FeedbackLifecycleError):
        await manager.resolve(feedback.feedback_id)  # must be handled first
    await manager.acknowledge(feedback.feedback_id)
    await manager.mark_handled(feedback.feedback_id)
    await manager.resolve(feedback.feedback_id, resolution={"applied_to": "faq"})

    (stored,) = await manager.query(FeedbackQuery(category="correction"))
    assert stored.status is FeedbackStatus.RESOLVED
    assert stored.resolution == {"applied_to": "faq"}
    assert sink.names == [
        "feedback.received",
        "feedback.acknowledged",
        "feedback.handled",
        "feedback.resolved",
    ]


class Messages(TypedDict):
    messages: list[Any]


@tool
async def lookup(city: str) -> str:
    """Look up a city."""
    raise TimeoutError(f"lookup for {city!r} timed out")


def build_agent() -> Any:
    # Replace with your graph; a node can stand in for the model call.
    def plan(state: Messages) -> Messages:
        call = {"name": "lookup", "args": {"city": "Canberra"}, "id": "call-1"}
        return {"messages": [AIMessage(content="", tool_calls=[call])]}

    builder = StateGraph(Messages)
    builder.add_node("plan", plan)
    builder.add_node("tools", ToolNode([lookup], handle_tool_errors=False))
    builder.add_edge(START, "plan")
    builder.add_edge("plan", "tools")
    builder.add_edge("tools", END)
    return builder.compile()


async def test_tool_failures_are_recorded_once(manager: FeedbackManager) -> None:
    with pytest.raises(TimeoutError):
        await build_agent().ainvoke(
            {"messages": []}, {"callbacks": [FeedbackCallbackHandler(manager)]}
        )

    (feedback,) = await manager.query()
    assert (feedback.source, feedback.category) == ("tool", "timeout")
    assert feedback.target == FeedbackTarget(type="tool_call", id="call-1")


class Approval(TypedDict, total=False):
    action: str
    decision: str


def build_approval_graph() -> Any:
    # Replace with your graph; interrupts need a checkpointer.
    def confirm(state: Approval) -> Approval:
        return {"decision": interrupt({"question": f"Run {state['action']}?"})}

    builder = StateGraph(Approval)
    builder.add_node("confirm", confirm)
    builder.add_edge(START, "confirm")
    builder.add_edge("confirm", END)
    return builder.compile(checkpointer=InMemorySaver())


@pytest.mark.parametrize(
    ("response", "approved", "status"),
    [("approved", True, FeedbackStatus.RESOLVED), ("rejected", False, FeedbackStatus.REJECTED)],
)
async def test_human_decisions_are_recorded(
    manager: FeedbackManager, response: str, approved: bool, status: FeedbackStatus
) -> None:
    graph = build_approval_graph()
    bridge = HumanInTheLoopBridge(manager)
    config: Any = {"configurable": {"thread_id": f"approval-{response}"}}

    (pending,) = extract_interrupts(await graph.ainvoke({"action": "refund"}, config))
    request = await bridge.request(
        target=FeedbackTarget(type="node", id="confirm"),
        interrupt=pending,
        execution_context=execution_context_from_snapshot(await graph.aget_state(config)),
    )
    await bridge.resolve(request.feedback_id, response=response, approved=approved)
    result = await graph.ainvoke(bridge.resume_command(response), config)

    stored = await manager.get(request.feedback_id)
    assert stored is not None
    assert stored.status is status
    assert stored.resolution == {"response": response, "approved": approved}
    assert result["decision"] == response


class ReviewQueue(FeedbackHandler):
    """Replace with your handler."""

    def __init__(self) -> None:
        self.received: list[FeedbackEvent] = []

    async def handle(self, feedback: FeedbackEvent) -> FeedbackHandlerResult:
        self.received.append(feedback)
        return FeedbackHandlerResult(handled=True)


async def test_corrections_reach_the_review_queue(sink: RecordingSink) -> None:
    queue = ReviewQueue()
    router = DefaultFeedbackRouter([RoutingRule(by_category("correction"), [queue])])
    manager = FeedbackManager(router=router, failure_policy=STRICT, observability_sink=sink)

    feedback = await manager.submit(**correction())
    await manager.submit(**correction(category="rating", idempotency_key="rating:gen-1"))

    assert [event.feedback_id for event in queue.received] == [feedback.feedback_id]
    assert sink.names.count("feedback.routed") == 1


class BrokenHandler(FeedbackHandler):
    async def handle(self, feedback: FeedbackEvent) -> FeedbackHandlerResult:
        raise ConnectionError("ticketing system unreachable")


async def test_handler_failures_surface_in_tests(sink: RecordingSink) -> None:
    router = DefaultFeedbackRouter([RoutingRule(by_category("correction"), [BrokenHandler()])])
    manager = FeedbackManager(router=router, failure_policy=STRICT, observability_sink=sink)

    with pytest.raises(FeedbackHandlerError) as caught:
        await manager.submit(**correction())

    assert isinstance(caught.value.__cause__, ConnectionError)
    assert len(await manager.query()) == 1  # stored before the handler ran
    assert "feedback.failed" in sink.names
