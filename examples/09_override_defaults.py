"""Example 9 -- Overriding every default collaborator of ``FeedbackManager``.

``FeedbackManager()`` is immediately usable with zero arguments -- every
dependency defaults to an in-memory/no-op implementation. This example
shows a developer how to override *each* of those defaults with a real,
working, minimal implementation, and proves (by comparing behavior against
a plain ``FeedbackManager()``) that the override actually took effect.

Overridden here:
    - ``store``               -- an audited wrapper around the in-memory store
    - ``router`` + a handler   -- routes every event to a custom handler
    - ``correlator``           -- tags every event with a custom correlation label
    - ``xai_runtime``          -- real ``langgraph-xai`` provenance instead of ``None``
    - ``lifecycle_policy``     -- adds a business rule on top of the base state machine
    - ``redaction_policy``     -- strips PII from payloads before persistence
    - ``failure_policy``       -- makes routing failures blocking instead of best-effort
    - ``observability_sink``   -- collects events in a list instead of logging them

Run with::

    uv run python examples/09_override_defaults.py
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph_xai import XAIRuntime

from feedback_manager import (
    FeedbackCategory,
    FeedbackEvent,
    FeedbackManager,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.contracts.correlator import FeedbackCorrelator
from feedback_manager.contracts.handler import (
    FeedbackContext,
    FeedbackHandler,
    FeedbackHandlerResult,
)
from feedback_manager.contracts.policy import FeedbackLifecyclePolicy, FeedbackPolicy
from feedback_manager.contracts.router import FeedbackRouter
from feedback_manager.core.context import CorrelationContext, ExecutionContext
from feedback_manager.core.status import FeedbackStatus
from feedback_manager.errors import FeedbackLifecycleError
from feedback_manager.observability.hooks import ObservabilityEvent, ObservabilitySink
from feedback_manager.policies.failure import FailureMode, FailurePolicy, FeedbackStage
from feedback_manager.storage.memory import InMemoryFeedbackStore


class AuditedStore(InMemoryFeedbackStore):
    """Override ``store``: same behavior as the default, plus a call log."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def create(self, feedback: FeedbackEvent) -> FeedbackEvent:
        self.calls.append(f"create({feedback.feedback_id})")
        return await super().create(feedback)


class AuditHandler(FeedbackHandler):
    """A handler the custom router below sends every event to."""

    def __init__(self) -> None:
        self.handled: list[UUID] = []

    async def handle(
        self, feedback: FeedbackEvent, context: FeedbackContext
    ) -> FeedbackHandlerResult:
        self.handled.append(feedback.feedback_id)
        return FeedbackHandlerResult(handled=True, detail="audited")


class RouteEverythingToAudit(FeedbackRouter):
    """Override ``router``: unlike the default (no handlers registered), route everything."""

    def __init__(self, handler: FeedbackHandler) -> None:
        self._handler = handler

    async def route(self, feedback: FeedbackEvent) -> Sequence[FeedbackHandler]:
        return (self._handler,)


class LabelingCorrelator(FeedbackCorrelator):
    """Override ``correlator``: tag every event's correlation with a fixed label."""

    async def correlate(
        self, feedback: FeedbackEvent, execution_context: ExecutionContext | None
    ) -> CorrelationContext:
        return CorrelationContext(
            execution=execution_context,
            correlation_id="override-demo-correlation",
        )


class RequireReviewerToResolve(FeedbackLifecyclePolicy):
    """Override ``lifecycle_policy``: business rule beyond the base state machine."""

    def authorize_transition(self, feedback: FeedbackEvent, target: FeedbackStatus) -> None:
        if target == FeedbackStatus.RESOLVED and "reviewer" not in feedback.metadata:
            raise FeedbackLifecycleError(
                f"cannot resolve {feedback.feedback_id}: metadata['reviewer'] is required"
            )


class RedactEmails(FeedbackPolicy):
    """Override ``redaction_policy``: strip a PII field before persistence/serialization."""

    def apply(self, feedback: FeedbackEvent) -> FeedbackEvent:
        if "email" not in feedback.payload:
            return feedback
        redacted_payload = {**feedback.payload, "email": "***redacted***"}
        return feedback.model_copy(update={"payload": redacted_payload})


class ListObservabilitySink(ObservabilitySink):
    """Override ``observability_sink``: collect events instead of logging them."""

    def __init__(self) -> None:
        self.events: list[ObservabilityEvent] = []

    def emit(self, event: ObservabilityEvent) -> None:
        self.events.append(event)


async def main() -> None:
    store = AuditedStore()
    handler = AuditHandler()
    sink = ListObservabilitySink()
    runtime = XAIRuntime(application_id="override-demo", tenant_id="acme", graph_id="demo-graph")

    manager = FeedbackManager(
        store=store,
        router=RouteEverythingToAudit(handler),
        correlator=LabelingCorrelator(),
        xai_runtime=runtime,
        lifecycle_policy=RequireReviewerToResolve(),
        redaction_policy=RedactEmails(),
        failure_policy=FailurePolicy(modes={FeedbackStage.ROUTING: FailureMode.BLOCKING}),
        observability_sink=sink,
    )

    # Provenance is only resolvable while a langgraph-xai-instrumented run is
    # active, so -- unlike the other overrides -- this one is demonstrated by
    # submitting from inside a real, instrumented LangGraph node.
    async def submit_node(state: dict[str, str]) -> dict[str, str]:
        submitted = await manager.submit(
            source=FeedbackSource.HUMAN,
            category=FeedbackCategory.COMMENT,
            target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id="override-demo"),
            payload={"comment": "great answer", "email": "someone@example.com"},
            metadata={},
        )
        return {"feedback_id": str(submitted.feedback_id)}

    graph = StateGraph(dict)
    graph.add_node("submit_feedback", submit_node)
    graph.add_edge(START, "submit_feedback")
    graph.add_edge("submit_feedback", END)
    instrumented = runtime.instrument(graph.compile())
    result = await instrumented.ainvoke(
        {}, config={"configurable": {"thread_id": "override-demo-thread"}}
    )
    event = await manager.get(UUID(result["feedback_id"]))

    print("== store override ==")
    print(f"AuditedStore.calls: {store.calls}")

    print("\n== router + handler override ==")
    print(f"AuditHandler.handled: {handler.handled} (expected: [{event.feedback_id}])")

    print("\n== correlator override ==")
    print(f"correlation_id: {event.correlation.correlation_id}")

    print("\n== xai_runtime override (real langgraph-xai provenance) ==")
    print(f"provenance: {event.provenance}")

    print("\n== redaction_policy override ==")
    print(f"payload persisted (email redacted): {event.payload}")

    print("\n== lifecycle_policy override ==")
    await manager.acknowledge(event.feedback_id)
    await manager.mark_handled(event.feedback_id)
    try:
        await manager.resolve(event.feedback_id, resolution={"reason": "looks good"})
    except FeedbackLifecycleError as exc:
        print(f"Blocked as expected (no metadata['reviewer']): {exc}")

    print("\n== failure_policy override (routing made BLOCKING) ==")

    class ExplodingRouter(FeedbackRouter):
        async def route(self, feedback: FeedbackEvent) -> Sequence[FeedbackHandler]:
            raise RuntimeError("router is broken")

    broken_manager = FeedbackManager(
        router=ExplodingRouter(),
        failure_policy=FailurePolicy(modes={FeedbackStage.ROUTING: FailureMode.BLOCKING}),
    )
    try:
        await broken_manager.submit(
            source=FeedbackSource.HUMAN,
            category=FeedbackCategory.COMMENT,
            target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id="failure-demo"),
            payload={},
        )
    except RuntimeError as exc:
        print(f"Propagated as expected (default would have swallowed this): {exc!r}")

    print("\n== observability_sink override ==")
    print(f"Collected {len(sink.events)} observability events:")
    for observed in sink.events:
        print(f"  - {observed.name} feedback_id={observed.feedback_id}")


if __name__ == "__main__":
    asyncio.run(main())
