# Test your integration

feedback-manager needs no infrastructure in tests: `FeedbackManager()` keeps
everything in memory, and every collaborator can be replaced by a small test
double. These tests use [pytest](https://docs.pytest.org/) with
[pytest-asyncio](https://pytest-asyncio.readthedocs.io/) in `auto` mode.

## A strict manager fixture

Two choices make tests precise:

- a `BLOCKING` failure policy for every stage, so a broken handler or
  subscriber fails the test instead of logging a warning;
- a recording observability sink, to assert on what happened.

```python
import pytest

from feedback_manager import FeedbackManager
from feedback_manager.observability import ObservabilityEvent
from feedback_manager.policies import FailureMode, FailurePolicy, FeedbackStage


class RecordingSink:
    """Keeps every observability event, for assertions."""

    def __init__(self) -> None:
        self.events: list[ObservabilityEvent] = []

    def emit(self, event: ObservabilityEvent) -> None:
        self.events.append(event)


@pytest.fixture
def sink() -> RecordingSink:
    return RecordingSink()


@pytest.fixture
def manager(sink: RecordingSink) -> FeedbackManager:
    # Every stage raises, so a broken handler or subscriber fails the test.
    strict = FailurePolicy(modes=dict.fromkeys(FeedbackStage, FailureMode.BLOCKING))
    return FeedbackManager(failure_policy=strict, observability_sink=sink)
```

Create a new manager per test: managers share no state, so tests stay
independent.

## Assert on stored feedback and events

```python
from feedback_manager import FeedbackQuery, FeedbackStatus, FeedbackTarget


async def test_corrections_are_resolved(manager: FeedbackManager, sink: RecordingSink) -> None:
    feedback = await manager.submit(
        source="human",
        category="correction",
        target=FeedbackTarget(type="generation", id="gen-42"),
        payload={"corrected_text": "Canberra"},
    )
    await manager.acknowledge(feedback.feedback_id)
    await manager.mark_handled(feedback.feedback_id)
    await manager.resolve(feedback.feedback_id)

    (stored,) = await manager.query(FeedbackQuery(category="correction"))
    assert stored.status is FeedbackStatus.RESOLVED
    assert [event.name for event in sink.events] == [
        "feedback.received",
        "feedback.acknowledged",
        "feedback.handled",
        "feedback.resolved",
    ]
```

## Test failure capture without a model

Graph nodes and tools can stand in for model calls, so failure capture is
testable offline and deterministically:

```python
from typing import Any, TypedDict

from langchain_core.messages import AIMessage, AnyMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from feedback_manager.integrations.langchain import FeedbackCallbackHandler


class State(TypedDict):
    messages: list[AnyMessage]


@tool
async def fetch_weather(city: str) -> str:
    """Look up the weather for a city."""
    raise TimeoutError("weather service timed out")


def plan(state: State) -> State:
    call = {"name": "fetch_weather", "args": {"city": "Canberra"}, "id": "call-1"}
    return {"messages": [AIMessage(content="", tool_calls=[call])]}


def build_graph() -> Any:
    builder = StateGraph(State)
    builder.add_node("plan", plan)
    builder.add_node("tools", ToolNode([fetch_weather], handle_tool_errors=False))
    builder.add_edge(START, "plan")
    builder.add_edge("plan", "tools")
    builder.add_edge("tools", END)
    return builder.compile()


async def test_tool_timeouts_are_recorded_once(manager: FeedbackManager) -> None:
    with pytest.raises(TimeoutError):
        await build_graph().ainvoke(
            {"messages": []}, {"callbacks": [FeedbackCallbackHandler(manager)]}
        )

    (feedback,) = await manager.query()
    assert (feedback.source, feedback.category) == ("tool", "timeout")
    assert feedback.target == FeedbackTarget(type="tool_call", id="call-1")
```

For model failures, LangChain's fake chat models, such as
`langchain_core.language_models.GenericFakeChatModel`, or a model subclass that
raises, exercise the same path.

## Human-in-the-loop flows

Compile the graph with `langgraph.checkpoint.memory.InMemorySaver`, then drive
the pause, the request, the decision, and the resume in one test, exactly as in
[example 02](../examples/basics.md#02-human-in-the-loop-approval). Assert on
the request's status and resolution, and on the graph's final result.

## Time-dependent behavior

`expire_overdue` and `RetentionPolicy` accept `now`, so expiry is testable
without waiting. See [expiring stale feedback](expire-feedback.md#testing-expiry).

## Your own store

Run the [store contract tests](custom-store.md#test-your-store) against it.
