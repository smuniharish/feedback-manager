# feedback-manager recipes

Complete patterns for common integration tasks. Each one is independent.

## Contents

- [Record feedback from your application](#record-feedback-from-your-application)
- [Capture agent failures](#capture-agent-failures)
- [Capture failures of direct tool calls](#capture-failures-of-direct-tool-calls)
- [Record human-in-the-loop approvals](#record-human-in-the-loop-approvals)
- [Route evaluator scores to a review queue](#route-evaluator-scores-to-a-review-queue)
- [Link feedback to langgraph-xai provenance](#link-feedback-to-langgraph-xai-provenance)
- [Store feedback in a database](#store-feedback-in-a-database)
- [Redact personal data](#redact-personal-data)
- [Enforce business rules on transitions](#enforce-business-rules-on-transitions)
- [Choose failure modes](#choose-failure-modes)
- [Expire stale feedback](#expire-stale-feedback)
- [React to every change](#react-to-every-change)
- [Send metrics to your monitoring](#send-metrics-to-your-monitoring)

## Record feedback from your application

Submit where the feedback originates, such as an API handler. Derive an
idempotency key from what makes the feedback unique, so client retries do not
create duplicates.

```python
from feedback_manager import ExecutionContext, FeedbackManager, FeedbackTarget, FeedbackTargetType

manager = FeedbackManager()


async def rate_answer(generation_id: str, thread_id: str, user_id: str, score: int) -> str:
    feedback = await manager.submit(
        source="human",
        category="rating",
        target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id=generation_id),
        payload={"score": score},
        metadata={"user_id": user_id},
        execution_context=ExecutionContext(thread_id=thread_id),
        idempotency_key=f"rating:{generation_id}:{user_id}",
    )
    return str(feedback.feedback_id)
```

Then move it through the lifecycle as it is worked on:

```python
await manager.acknowledge(feedback_id)
await manager.mark_handled(feedback_id)
await manager.resolve(feedback_id, resolution={"applied_to": "faq"})
# or: await manager.reject(feedback_id, reason="duplicate")
```

## Capture agent failures

Add the callback handler to the run's callbacks. It records each tool, model,
retriever, and node failure once, with the thread, node, and tool call, and
never changes how the exception propagates.

```python
from langchain_core.runnables import RunnableConfig

from feedback_manager.integrations.langchain import FeedbackCallbackHandler

config: RunnableConfig = {
    "callbacks": [FeedbackCallbackHandler(manager)],
    "configurable": {"thread_id": thread_id},
}
result = await graph.ainvoke(inputs, config)
```

Failures caught by a `ToolNode` or your own code are still recorded. Success
and LangGraph interrupts are not. A cancelled run is recorded once, about the
top-level run, and a node failure without the cancellations of the nodes
LangGraph stops because of it. Node timeouts are recorded about the node.

## Capture failures of direct tool calls

Calls that bypass LangChain runnables, such as direct MCP client calls,
produce no callbacks. Wrap them:

```python
from feedback_manager.integrations.langchain import capture_tool_feedback

async with capture_tool_feedback(
    manager, tool_call_id=call_id, tool_name="search", execution_context=context
):
    result = await session.call_tool("search", arguments)
```

The original exception always propagates unchanged. A cancellation of the
task running the call passes through unrecorded.

## Record human-in-the-loop approvals

The graph needs a checkpointer and a `thread_id`. Pause with LangGraph's
`interrupt()`, record the request after the graph paused, record the decision,
and resume.

```python
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt

from feedback_manager import FeedbackTarget, FeedbackTargetType
from feedback_manager.integrations.langgraph import (
    HumanInTheLoopBridge,
    execution_context_from_snapshot,
    extract_interrupts,
)


def confirm(state: State) -> State:
    decision = interrupt({"question": "Send the refund email?", "action": state["action"]})
    return {"action": state["action"], "decision": decision}


graph = builder.compile(checkpointer=InMemorySaver())
bridge = HumanInTheLoopBridge(manager)
config = {"configurable": {"thread_id": "refund-1042"}}

paused = await graph.ainvoke({"action": "send_refund_email"}, config)
(pending,) = extract_interrupts(paused)
snapshot = await graph.aget_state(config)
request = await bridge.request(
    target=FeedbackTarget(type=FeedbackTargetType.NODE, id="confirm"),
    interrupt=pending,
    execution_context=execution_context_from_snapshot(snapshot),
)

# Later, when the reviewer decides:
await bridge.resolve(request.feedback_id, response="approved", approved=True)
result = await graph.ainvoke(bridge.resume_command("approved"), config)
```

Record the same `response` you resume with. `approved=False` rejects the
request. For several pending interrupts, pass
`resume_command(response, interrupt_id=pending.id)`.

## Route evaluator scores to a review queue

```python
from feedback_manager import FeedbackEvent, FeedbackManager
from feedback_manager.contracts import FeedbackHandler, FeedbackHandlerResult
from feedback_manager.routing import DefaultFeedbackRouter, RoutingRule, all_of, by_source


class ReviewQueue(FeedbackHandler):
    async def handle(self, feedback: FeedbackEvent) -> FeedbackHandlerResult:
        await enqueue_for_review(feedback.feedback_id)  # your queue
        return FeedbackHandlerResult(handled=True, detail="queued for review")


def low_score(feedback: FeedbackEvent) -> bool:
    score = feedback.payload.get("score")
    return isinstance(score, float) and score < 0.5


router = DefaultFeedbackRouter(
    [RoutingRule(all_of(by_source("evaluator"), low_score), [ReviewQueue()], name="low scores")]
)
manager = FeedbackManager(router=router)
```

Handlers run in sequence before `submit` returns; keep them fast. Check value
types in predicates: payloads are arbitrary JSON.

## Link feedback to langgraph-xai provenance

Share one runtime between the instrumented graph and the manager:

```python
from langchain_core.runnables import RunnableConfig
from langgraph_xai import XAIRuntime

from feedback_manager import ExecutionContext, FeedbackManager
from feedback_manager.integrations.langgraph import execution_context_from_config

xai = XAIRuntime(application_id="support-bot", tenant_id="acme", graph_id="refunds")
manager = FeedbackManager(xai_runtime=xai)
graph = xai.instrument(builder.compile())


async def report(state: State, config: RunnableConfig) -> State:
    # During the run: provenance includes the latest decision and its evidence.
    await manager.submit(..., execution_context=execution_context_from_config(config))
    return {}


# After the run: pass the langgraph-xai run ID.
with xai.collect_runs() as runs:
    await graph.ainvoke(inputs)
(run,) = runs
await manager.submit(..., execution_context=ExecutionContext(run_id=str(run.run_id)))
```

Type the node's `config` parameter as `RunnableConfig`, or LangGraph does not
pass it. Decision and evidence IDs are only available during the run.

## Store feedback in a database

Subclass `FeedbackStore`. Store the event as JSON
(`model_dump_json`, `model_validate_json`) next to indexed columns for the
query filters, and an insertion sequence for ordering.

```python
from feedback_manager import (
    FeedbackConflictError,
    FeedbackEvent,
    FeedbackNotFoundError,
    validate_transition,
)
from feedback_manager.contracts import FeedbackStore


class MyStore(FeedbackStore):
    async def transition(self, feedback_id, status, *, expected, resolution=None):
        current = await self.get(feedback_id)  # inside one transaction or row lock
        if current is None:
            raise FeedbackNotFoundError("unknown feedback event", feedback_id=feedback_id)
        if current.status != expected:
            raise FeedbackConflictError("status changed", feedback_id=feedback_id)
        if validate_transition(feedback_id, expected, status).idempotent:
            return current
        updated = current.with_status(status, resolution=resolution)
        ...  # UPDATE ... WHERE feedback_id = ? AND status = expected
        return updated

    # create: INSERT ... ON CONFLICT (idempotency_key) DO NOTHING, then return the stored event
    # get: SELECT by feedback_id
    # query: WHERE filters, ORDER BY sequence (DESC when newest_first), LIMIT;
    #   index idempotency_key: submit looks it up before processing
```

Complete, tested implementations:
[examples/sqlite_feedback_store.py](https://github.com/smuniharish/feedback-manager/blob/master/examples/sqlite_feedback_store.py)
and
[examples/postgres_feedback_store.py](https://github.com/smuniharish/feedback-manager/blob/master/examples/postgres_feedback_store.py).
Run the
[store contract tests](https://github.com/smuniharish/feedback-manager/blob/master/tests/examples/test_stores.py)
against a new store.

## Redact personal data

The redaction policy runs before anything else sees the event. If it fails,
nothing is stored.

```python
from feedback_manager import FeedbackEvent, FeedbackManager
from feedback_manager.contracts import FeedbackRedactionPolicy


class DropContactDetails(FeedbackRedactionPolicy):
    def redact(self, feedback: FeedbackEvent) -> FeedbackEvent:
        payload = {k: v for k, v in feedback.payload.items() if k not in {"email", "phone"}}
        return feedback.model_copy(update={"payload": payload})


manager = FeedbackManager(redaction_policy=DropContactDetails())
```

## Enforce business rules on transitions

```python
from feedback_manager import FeedbackEvent, FeedbackLifecycleError, FeedbackStatus
from feedback_manager.contracts import FeedbackLifecyclePolicy


class ReviewerResolves(FeedbackLifecyclePolicy):
    def authorize_transition(self, feedback: FeedbackEvent, target: FeedbackStatus) -> None:
        if target is FeedbackStatus.RESOLVED and "reviewer" not in feedback.metadata:
            raise FeedbackLifecycleError("assign a reviewer before resolving")
```

The policy sees the current state before every move, including retries after
concurrent changes. For rules about the caller, read the caller from your
request context, for example a `contextvars.ContextVar`.

## Choose failure modes

```python
from feedback_manager.policies import FailureMode, FailurePolicy, FeedbackStage

production = FailurePolicy(modes={FeedbackStage.HANDLER: FailureMode.BLOCKING})
tests = FailurePolicy(modes=dict.fromkeys(FeedbackStage, FailureMode.BLOCKING))
```

Unlisted stages stay best-effort: logged, `feedback.failed` emitted, processing
continues. Alert on `feedback.failed` grouped by `stage`.

## Expire stale feedback

The package runs no scheduler; call it from yours:

```python
from datetime import timedelta

from feedback_manager.policies import RetentionPolicy

RETENTION = RetentionPolicy(max_pending_age=timedelta(days=7))
expired = await manager.expire_overdue(RETENTION)
```

`HANDLED` feedback does not expire. Pass `now=` in tests.

## React to every change

```python
from feedback_manager import FeedbackEvent, FeedbackQuery


async def publish(feedback: FeedbackEvent) -> None:
    await broker.publish("feedback", feedback.model_dump_json())  # your broker


subscription = manager.subscribe(publish)  # subscription.cancel() to stop

async with manager.stream(FeedbackQuery(category="correction")) as stream:
    async for feedback in stream:
        render(feedback)  # your UI
```

Subscribers and streams are in-process and not durable; the store is the
system of record. Changes made concurrently to one event can arrive out of
order: keep the event with the latest `updated_at`, or re-read it with `get`.

## Send metrics to your monitoring

```python
from feedback_manager.observability import ObservabilityEvent

LABELS = ("source", "category", "stage")


class MetricsSink:
    def emit(self, event: ObservabilityEvent) -> None:
        labels = {key: str(event.attributes[key]) for key in LABELS if key in event.attributes}
        counter.add(1, {"event": event.name, **labels})  # your metrics client


manager = FeedbackManager(observability_sink=MetricsSink())
```

`emit` runs inline: hand network calls to a background worker.
