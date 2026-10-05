"""Replace every default collaborator of `FeedbackManager`, and show each one taking effect.

`FeedbackManager()` works with no arguments. Each keyword argument swaps one
default for your own implementation of the matching contract:

- ``store``: where feedback is kept;
- ``router``: which handlers run for new feedback;
- ``correlator``: how related feedback is grouped;
- ``xai_runtime``: links feedback to langgraph-xai provenance;
- ``lifecycle_policy``: business rules on lifecycle moves;
- ``redaction_policy``: removes sensitive data before storage;
- ``failure_policy``: which stage failures propagate instead of being isolated;
- ``observability_sink``: where observability events go.

Run with:

    uv run python examples/09_override_defaults.py
"""

import asyncio
from collections.abc import Sequence
from typing import TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph_xai import XAIRuntime

from feedback_manager import (
    ExecutionContext,
    FeedbackEvent,
    FeedbackLifecycleError,
    FeedbackManager,
    FeedbackStatus,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.contracts import (
    FeedbackHandler,
    FeedbackHandlerResult,
    FeedbackLifecyclePolicy,
    FeedbackRedactionPolicy,
    FeedbackRouter,
)
from feedback_manager.errors import FeedbackRoutingError
from feedback_manager.observability import ObservabilityEvent
from feedback_manager.policies import FailureMode, FailurePolicy, FeedbackStage
from feedback_manager.storage import InMemoryFeedbackStore


class CountingStore(InMemoryFeedbackStore):
    """``store``: the in-memory store, counting writes."""

    def __init__(self) -> None:
        super().__init__()
        self.writes = 0

    async def create(self, feedback: FeedbackEvent) -> FeedbackEvent:
        self.writes += 1
        return await super().create(feedback)


class Notifier(FeedbackHandler):
    """A handler that would page the on-call reviewer."""

    def __init__(self) -> None:
        self.notified: list[UUID] = []

    async def handle(self, feedback: FeedbackEvent) -> FeedbackHandlerResult:
        self.notified.append(feedback.feedback_id)
        return FeedbackHandlerResult(handled=True, detail="paged on-call")


class EverythingToNotifier(FeedbackRouter):
    """``router``: unlike the default, route every event."""

    def __init__(self, handler: FeedbackHandler) -> None:
        self._handler = handler

    async def route(self, feedback: FeedbackEvent) -> Sequence[FeedbackHandler]:
        return (self._handler,)


class ByConversation:
    """``correlator``: group feedback by the conversation in its metadata."""

    async def correlate(self, feedback: FeedbackEvent) -> str:
        return str(feedback.metadata.get("conversation", feedback.feedback_id))


class ReviewerResolves(FeedbackLifecyclePolicy):
    """``lifecycle_policy``: only feedback assigned to a reviewer may be resolved."""

    def authorize_transition(self, feedback: FeedbackEvent, target: FeedbackStatus) -> None:
        if target is FeedbackStatus.RESOLVED and "reviewer" not in feedback.metadata:
            raise FeedbackLifecycleError("assign a reviewer before resolving")


class MaskEmails(FeedbackRedactionPolicy):
    """``redaction_policy``: mask e-mail addresses before storage."""

    def redact(self, feedback: FeedbackEvent) -> FeedbackEvent:
        if "email" not in feedback.payload:
            return feedback
        return feedback.model_copy(update={"payload": {**feedback.payload, "email": "***"}})


class CollectingSink:
    """``observability_sink``: keep events in memory instead of logging them."""

    def __init__(self) -> None:
        self.events: list[ObservabilityEvent] = []

    def emit(self, event: ObservabilityEvent) -> None:
        self.events.append(event)


class BrokenRouter(FeedbackRouter):
    async def route(self, feedback: FeedbackEvent) -> Sequence[FeedbackHandler]:
        raise LookupError("routing table unavailable")


async def run_instrumented_graph(xai: XAIRuntime) -> str:
    """Run a one-node graph under langgraph-xai and return its run ID."""

    class State(TypedDict, total=False):
        answer: str

    builder = StateGraph(State)
    builder.add_node("answer", lambda state: {"answer": "Canberra"})
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    with xai.collect_runs() as runs:
        await xai.instrument(builder.compile()).ainvoke({})
    return str(runs[0].run_id)


async def main() -> None:
    store, notifier, sink = CountingStore(), Notifier(), CollectingSink()
    xai = XAIRuntime(application_id="support-bot", tenant_id="acme", graph_id="qa")
    manager = FeedbackManager(
        store=store,
        router=EverythingToNotifier(notifier),
        correlator=ByConversation(),
        xai_runtime=xai,
        lifecycle_policy=ReviewerResolves(),
        redaction_policy=MaskEmails(),
        failure_policy=FailurePolicy(modes={FeedbackStage.HANDLER: FailureMode.BLOCKING}),
        observability_sink=sink,
    )
    run_id = await run_instrumented_graph(xai)

    feedback = await manager.submit(
        source="human",
        category="comment",
        target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id="gen-7"),
        payload={"comment": "Great answer", "email": "someone@example.com"},
        metadata={"conversation": "conv-12"},
        execution_context=ExecutionContext(run_id=run_id),
    )
    print(f"store:              {store.writes} write(s)")
    print(f"router + handler:   notified {len(notifier.notified)} time(s)")
    print(f"correlator:         correlation_id={feedback.correlation_id}")
    print(f"xai_runtime:        {feedback.provenance and feedback.provenance.summary}")
    print(f"redaction_policy:   payload={feedback.payload}")

    await manager.acknowledge(feedback.feedback_id)
    await manager.mark_handled(feedback.feedback_id)
    try:
        await manager.resolve(feedback.feedback_id)
    except FeedbackLifecycleError as error:
        print(f"lifecycle_policy:   {error}")

    strict = FeedbackManager(
        router=BrokenRouter(),
        failure_policy=FailurePolicy(modes={FeedbackStage.ROUTING: FailureMode.BLOCKING}),
        observability_sink=sink,
    )
    try:
        await strict.submit(
            source="human",
            category="comment",
            target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id="gen-8"),
        )
    except FeedbackRoutingError as error:
        print(f"failure_policy:     {error}")

    print(f"observability_sink: {[event.name for event in sink.events]}")


if __name__ == "__main__":
    asyncio.run(main())
