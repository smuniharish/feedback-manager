# feedback-manager API reference

Signatures for `feedback-manager` 0.1.1. Everything not listed here is
internal. All I/O methods are coroutines.

## Contents

- [Imports](#imports)
- [FeedbackManager](#feedbackmanager)
- [Models](#models)
- [Lifecycle](#lifecycle)
- [Queries](#queries)
- [Contracts](#contracts)
- [Routing](#routing)
- [Policies](#policies)
- [Observability](#observability)
- [Integrations](#integrations)
- [Errors](#errors)

## Imports

```python
from feedback_manager import (
    ExecutionContext,
    FeedbackCategory,
    FeedbackEvent,
    FeedbackManager,
    FeedbackProvenanceReference,
    FeedbackQuery,
    FeedbackSource,
    FeedbackStatus,
    FeedbackStream,
    FeedbackTarget,
    FeedbackTargetType,
    Subscription,
    validate_transition,
    __version__,
    # every error class, listed under Errors
)
from feedback_manager.core import (
    Identifier,
    JsonObject,
    LEGAL_TRANSITIONS,
    TERMINAL_STATUSES,
    LifecycleTransition,
    is_legal_transition,
)
from feedback_manager.contracts import (
    FeedbackCorrelator,
    FeedbackHandler,
    FeedbackHandlerResult,
    FeedbackLifecyclePolicy,
    FeedbackRedactionPolicy,
    FeedbackRouter,
    FeedbackStore,
    FeedbackSubscriber,
)
from feedback_manager.storage import InMemoryFeedbackStore
from feedback_manager.correlation import DefaultFeedbackCorrelator, default_correlation_id
from feedback_manager.routing import (
    DefaultFeedbackRouter,
    RoutingRule,
    all_of,
    any_of,
    by_category,
    by_source,
    by_target_type,
)
from feedback_manager.handlers import AuditFeedbackHandler
from feedback_manager.policies import FailureMode, FailurePolicy, FeedbackStage, RetentionPolicy
from feedback_manager.observability import (
    LoggingObservabilitySink,
    NoOpObservabilitySink,
    ObservabilityEvent,
    ObservabilitySink,
    FEEDBACK_RECEIVED,
    FEEDBACK_ROUTED,
    FEEDBACK_ACKNOWLEDGED,
    FEEDBACK_HANDLED,
    FEEDBACK_RESOLVED,
    FEEDBACK_REJECTED,
    FEEDBACK_CANCELLED,
    FEEDBACK_EXPIRED,
    FEEDBACK_FAILED,
)
from feedback_manager.integrations.langchain import (
    FeedbackCallbackHandler,
    capture_tool_feedback,
    category_for_error,
)
from feedback_manager.integrations.langgraph import (
    HumanInTheLoopBridge,
    INTERRUPT_KEY,
    execution_context_from_config,
    execution_context_from_snapshot,
    extract_interrupts,
)
from feedback_manager.integrations.xai import XAIProvenanceAdapter
```

## FeedbackManager

```python
FeedbackManager(
    *,
    store: FeedbackStore | None = None,                    # InMemoryFeedbackStore()
    router: FeedbackRouter | None = None,                  # DefaultFeedbackRouter(): routes nothing
    correlator: FeedbackCorrelator | None = None,          # DefaultFeedbackCorrelator()
    xai_runtime: langgraph_xai.XAIRuntime | None = None,   # None: no provenance
    lifecycle_policy: FeedbackLifecyclePolicy | None = None,
    redaction_policy: FeedbackRedactionPolicy | None = None,
    failure_policy: FailurePolicy | None = None,           # best-effort for every stage
    observability_sink: ObservabilitySink | None = None,   # LoggingObservabilitySink()
)
```

A wrong collaborator type raises `FeedbackConfigurationError`. Managers share
no state.

| Method | Behavior |
|---|---|
| `await submit(*, source, category, target, payload=None, metadata=None, execution_context=None, idempotency_key=None, feedback_type=None) -> FeedbackEvent` | Validate, redact, correlate, resolve provenance, store as `RECEIVED`, publish, route. A stored `idempotency_key` returns the stored event before any processing. |
| `await acknowledge(feedback_id)` | `RECEIVED` to `ACKNOWLEDGED`. |
| `await mark_handled(feedback_id)` | `ACKNOWLEDGED` to `HANDLED`. |
| `await resolve(feedback_id, *, resolution=None)` | `HANDLED` to `RESOLVED`. |
| `await reject(feedback_id, *, reason=None, resolution=None)` | To `REJECTED`; `reason` is added to `resolution["reason"]`. |
| `await cancel(feedback_id, *, reason=None, resolution=None)` | To `CANCELLED`. |
| `await expire(feedback_id)` | Pending feedback to `EXPIRED`. |
| `await expire_overdue(policy, *, now=None) -> list[FeedbackEvent]` | Expire every overdue pending event and return the ones this call expired; skips events that changed, were deleted, or are denied. Safe to run from several workers. |
| `await get(feedback_id) -> FeedbackEvent \| None` | One event. |
| `await query(query=None) -> Sequence[FeedbackEvent]` | Matching events; all when `query` is `None`. |
| `subscribe(subscriber) -> Subscription` | Call an async callable with every new event and lifecycle change. |
| `stream(query=None) -> FeedbackStream` | Async iterator of changes from now on; call inside a running event loop. |
| `await aclose()` | Close every open stream; the manager stays usable. Also `async with manager:`. |

Lifecycle methods return the updated event, and are idempotent: moving to the
current status returns the event unchanged and publishes nothing. They raise
`FeedbackNotFoundError`, `FeedbackLifecycleError` (illegal or denied), and
`FeedbackStoreError`. `resolution` must be JSON.

`Subscription`: `.active`, `.cancel()`, and a context manager.
`FeedbackStream`: `async for`, `.closed`, `await .aclose()`, `async with`.

## Models

All models are frozen pydantic models. Building one directly raises pydantic's
`ValidationError`; `submit` raises `FeedbackValidationError`.

`FeedbackEvent` fields: `feedback_id: UUID`, `idempotency_key`, `source`,
`category`, `feedback_type`, `target: FeedbackTarget`, `payload: JsonObject`,
`metadata: JsonObject`, `execution_context: ExecutionContext | None`,
`correlation_id`, `provenance: FeedbackProvenanceReference | None`,
`status: FeedbackStatus`, `resolution: JsonObject | None`, `created_at`,
`updated_at` (timezone-aware). `with_status(status, *, resolution=None)`
returns a moved copy without checking the lifecycle; its `updated_at` is
always later than the original's, so `updated_at` orders an event's changes.

`FeedbackTarget(type, id, metadata={})`.

`ExecutionContext` (every field optional): `application_id`, `tenant_id`,
`graph_id`, `thread_id`, `run_id`, `checkpoint_id`, `node_id`, `task_id`,
`message_id`, `tool_call_id`, `generation_id`, `interrupt_id`, `metadata`.

`FeedbackProvenanceReference`: `run_id`, `execution_id`, `summary`,
`node_execution_id`, `tool_execution_id`, `human_interaction_id`,
`decision_id`, `evidence_ids`, `metadata`.

Validation: identifiers (`Identifier`) are non-empty strings without
surrounding whitespace; `JsonObject` is `dict[str, JsonValue]` with finite
numbers.

Open values are `str` subclasses: any non-empty string is valid, and
`known_values()` lists the constants.

| Type | Constants |
|---|---|
| `FeedbackSource` | `HUMAN`, `AGENT`, `GENERATION`, `TOOL`, `EVALUATOR`, `APPLICATION`, `SYSTEM`, `EXTERNAL` |
| `FeedbackCategory` | `APPROVAL`, `REJECTION`, `CORRECTION`, `RATING`, `COMMENT`, `INTERRUPTION`, `CANCELLATION`, `FAILURE`, `TIMEOUT`, `VALIDATION`, `QUALITY`, `UNCERTAINTY`, `REQUEST_FOR_HUMAN`, `PARTIAL_RESULT`, `COMPLETION` |
| `FeedbackTargetType` | `APPLICATION`, `AGENT`, `GRAPH`, `THREAD`, `RUN`, `CHECKPOINT`, `NODE`, `TASK`, `TOOL_CALL`, `TOOL_RESULT`, `GENERATION`, `MESSAGE`, `STATE` |

Constant values are the lowercase names, such as `"request_for_human"`.

## Lifecycle

| From | To |
|---|---|
| `CREATED` | `RECEIVED`, `CANCELLED`, `EXPIRED` |
| `RECEIVED` | `ACKNOWLEDGED`, `REJECTED`, `CANCELLED`, `EXPIRED` |
| `ACKNOWLEDGED` | `HANDLED`, `REJECTED`, `CANCELLED`, `EXPIRED` |
| `HANDLED` | `RESOLVED`, `REJECTED`, `CANCELLED` |
| `RESOLVED`, `REJECTED`, `CANCELLED`, `EXPIRED` | Terminal |

Stored feedback starts at `RECEIVED`. `is_legal_transition(current, target)`
returns a bool; `validate_transition(feedback_id, current, target)` returns a
`LifecycleTransition` with `idempotent`, or raises `FeedbackLifecycleError`.

## Queries

```python
FeedbackQuery(
    *, source=None, category=None, target_type=None, target_id=None,
    status=None,            # FeedbackStatus or its value
    correlation_id=None,
    idempotency_key=None,   # the event submitted with this key
    created_after=None,     # inclusive, timezone-aware
    created_before=None,    # exclusive, timezone-aware
    newest_first=False,
    limit=None,             # positive int
)
```

Invalid values raise `FeedbackValidationError` on construction.
`query.matches(event)` applies the filters (not `limit` or order). Results are
in creation order. Streams ignore `limit` and `newest_first`.

## Contracts

```python
class FeedbackStore(ABC):
    async def create(self, feedback: FeedbackEvent) -> FeedbackEvent: ...
        # stored event for a known idempotency_key; FeedbackStoreError for a duplicate feedback_id
    async def get(self, feedback_id: UUID) -> FeedbackEvent | None: ...
    async def transition(self, feedback_id, status, *, expected, resolution=None) -> FeedbackEvent: ...
        # compare-and-set; FeedbackNotFoundError, FeedbackConflictError (status != expected),
        # FeedbackLifecycleError (illegal); unchanged event when status == expected;
        # updated event from current.with_status(status, resolution=resolution)
    async def query(self, query: FeedbackQuery) -> Sequence[FeedbackEvent]: ...
        # creation order, newest first if asked, at most query.limit;
        # submit looks up idempotency_key before processing, so index it

class FeedbackRouter(ABC):
    async def route(self, feedback: FeedbackEvent) -> Sequence[FeedbackHandler]: ...

class FeedbackHandler(ABC):
    async def handle(self, feedback: FeedbackEvent) -> FeedbackHandlerResult: ...

FeedbackHandlerResult(handled: bool, detail: str | None = None)

class FeedbackLifecyclePolicy(ABC):
    def authorize_transition(self, feedback: FeedbackEvent, target: FeedbackStatus) -> None: ...
        # raise FeedbackLifecycleError to deny

class FeedbackRedactionPolicy(ABC):
    def redact(self, feedback: FeedbackEvent) -> FeedbackEvent: ...

class FeedbackCorrelator(Protocol):
    async def correlate(self, feedback: FeedbackEvent) -> str: ...  # non-empty, unpadded

class FeedbackSubscriber(Protocol):
    async def __call__(self, feedback: FeedbackEvent) -> None: ...
```

The default correlator uses `run_id`, then `thread_id`, then `checkpoint_id`,
then the `feedback_id`.

## Routing

`DefaultFeedbackRouter(rules=(), *, default_handlers=())` runs the handlers of
every matching rule, in order and without duplicates, or the default handlers
when nothing matches; `add_rule(rule)` appends. `RoutingRule(predicate,
handlers, name="rule")`. Predicates are `Callable[[FeedbackEvent], bool]`;
`by_source`, `by_category`, `by_target_type`, `any_of`, and `all_of` build
them. `AuditFeedbackHandler(logger_name="feedback_manager.audit")` logs events
without payloads. Routing runs once, at submission. A router must return
`FeedbackHandler` instances, and a handler a `FeedbackHandlerResult`;
anything else fails the `ROUTING` or `HANDLER` stage.

## Policies

`FailurePolicy(modes={FeedbackStage.HANDLER: FailureMode.BLOCKING})` overrides
the given stages; the rest stay `BEST_EFFORT`. Stages and their blocking errors:

| `FeedbackStage` | Blocking error |
|---|---|
| `CORRELATION`, `PROVENANCE` | `FeedbackCorrelationError` (nothing stored) |
| `ROUTING` | `FeedbackRoutingError` |
| `HANDLER` | `FeedbackHandlerError` |
| `SUBSCRIBER` | `FeedbackSubscriberError` |

Store failures always raise `FeedbackStoreError`; redaction failures raise
`FeedbackValidationError`; sink failures are always logged and ignored.

`RetentionPolicy(max_pending_age: timedelta)`: `cutoff(now=None)`,
`is_expired(feedback, *, now=None)`. Only `CREATED`, `RECEIVED`, and
`ACKNOWLEDGED` expire.

## Observability

`ObservabilitySink` is a protocol with `emit(event: ObservabilityEvent) ->
None`, called inline. `ObservabilityEvent(name, feedback_id, occurred_at,
attributes)`; attributes always include `source`, `category`, `target_type`,
and `status`; `feedback.routed` adds `handler_count` and `handled_count`;
`feedback.failed` adds `stage`, `error_type`, and `error`.

## Integrations

- `FeedbackCallbackHandler(manager)`: a LangChain async callback handler that
  records each tool, model, retriever, and chain failure once. Sources and
  targets: tool `TOOL`/`tool_call`, model `GENERATION`/`generation`,
  retriever `TOOL`/`run`, chain `AGENT`/`node` or `run`. `feedback_type` is
  `tool_error`, `model_error`, `retriever_error`, or `chain_error`; the payload
  has `error`, `error_type`, and `operation`. A cancelled run is recorded once,
  about the top-level run; the cancellations inside it, and those LangGraph
  causes when a node fails, are not. LangGraph node timeouts
  (`NodeTimeoutError`) and node-raised cancellations (`NodeCancelledError`)
  are recorded about the node.
- `capture_tool_feedback(manager, *, tool_call_id, tool_name=None,
  execution_context=None)`: async context manager recording a `TOOL` failure
  and re-raising it. Cancellations pass through unrecorded.
- `category_for_error(error)`: `TIMEOUT`, `CANCELLATION`, or `FAILURE`.
- `execution_context_from_config(config, *, node_id=None, tool_call_id=None,
  generation_id=None, message_id=None, interrupt_id=None)`.
- `execution_context_from_snapshot(snapshot)`: thread, checkpoint, run ID, and
  the single pending interrupt and its node.
- `HumanInTheLoopBridge(manager)`: `await request(*, target, interrupt=None,
  prompt=None, execution_context=None, category=REQUEST_FOR_HUMAN,
  metadata=None, idempotency_key=None)`; `await resolve(feedback_id, *,
  response, approved=None)` (rejects when `approved is False`, resolves
  otherwise); `resume_command(response, *, interrupt_id=None) -> Command`.
- `extract_interrupts(chunk) -> tuple[Interrupt, ...]` and `INTERRUPT_KEY`.
- `XAIProvenanceAdapter(runtime)`: `await resolve(feedback)` returns a
  `FeedbackProvenanceReference` or `None`.

## Errors

```text
FeedbackManagerError(message, *, feedback_id=None, **context)
├── FeedbackValidationError      (ValueError)
├── FeedbackConfigurationError
├── FeedbackNotFoundError        (LookupError)
├── FeedbackLifecycleError       (.current_status, .requested_status)
│   └── FeedbackConflictError
├── FeedbackStoreError
├── FeedbackCorrelationError
├── FeedbackRoutingError
├── FeedbackHandlerError
└── FeedbackSubscriberError
```

Wrapped exceptions are chained as `__cause__`; `.context` holds details such
as the failed `stage`.
