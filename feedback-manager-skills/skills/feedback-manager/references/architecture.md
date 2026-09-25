# Architecture

Source of truth: the
[architecture docs](https://feedback-manager.readthedocs.io/en/latest/architecture/ARCHITECTURE/)
(in particular
[ARCHITECTURE](https://feedback-manager.readthedocs.io/en/latest/architecture/ARCHITECTURE/),
[DOMAIN_MODEL](https://feedback-manager.readthedocs.io/en/latest/architecture/DOMAIN_MODEL/),
[LIFECYCLE](https://feedback-manager.readthedocs.io/en/latest/architecture/LIFECYCLE/),
[CORRELATION_MODEL](https://feedback-manager.readthedocs.io/en/latest/architecture/CORRELATION_MODEL/),
[PROVENANCE_MODEL](https://feedback-manager.readthedocs.io/en/latest/architecture/PROVENANCE_MODEL/),
[ROUTING_MODEL](https://feedback-manager.readthedocs.io/en/latest/architecture/ROUTING_MODEL/),
[FAILURE_MODEL](https://feedback-manager.readthedocs.io/en/latest/architecture/FAILURE_MODEL/),
[CONCURRENCY_MODEL](https://feedback-manager.readthedocs.io/en/latest/architecture/CONCURRENCY_MODEL/),
and
[RESPONSIBILITY_MATRIX](https://feedback-manager.readthedocs.io/en/latest/architecture/RESPONSIBILITY_MATRIX/))
and the
[`src/feedback_manager/`](https://github.com/smuniharish/feedback-manager/tree/master/src/feedback_manager)
package. Read the linked source, not just this summary, before asserting
internal behavior.

## What the package owns vs. what it does not

`feedback-manager` owns: the feedback domain model, its lifecycle, its
correlation to execution context, its persistence contract, its routing and
handling, its policies, and its observability events.

It does not own: agent execution, graph orchestration, LangGraph interrupts,
checkpoints, streaming, evaluators/LLM-as-judge logic, or business workflow.
Those stay with LangChain, LangGraph, `langgraph-xai`, and the host
application. See
[Responsibility matrix](https://feedback-manager.readthedocs.io/en/latest/architecture/RESPONSIBILITY_MATRIX/)
and
[Framework boundary model](https://feedback-manager.readthedocs.io/en/latest/architecture/FRAMEWORK_BOUNDARY_MODEL/)
for the authoritative split.

## Public entry point

[`FeedbackManager`](https://github.com/smuniharish/feedback-manager/blob/master/src/feedback_manager/api/manager.py)
is the single application service most code needs. Every dependency is an
optional constructor keyword argument with an in-memory/no-op default, so
`FeedbackManager()` is immediately usable, and every instance is fully
independent (no hidden global state):

```python
FeedbackManager(
    *,
    store=None,               # FeedbackStore, default InMemoryFeedbackStore
    router=None,              # FeedbackRouter, default DefaultFeedbackRouter
    correlator=None,          # FeedbackCorrelator, default DefaultFeedbackCorrelator
    xai_runtime=None,         # langgraph_xai.XAIRuntime for provenance
    provenance_adapter=None,  # pre-built XAIProvenanceAdapter (mutually exclusive with xai_runtime)
    lifecycle_policy=None,    # FeedbackLifecyclePolicy
    redaction_policy=None,    # FeedbackPolicy
    failure_policy=None,      # FailurePolicy, default FailurePolicy()
    observability_sink=None,  # ObservabilitySink, default LoggingObservabilitySink
)
```

`submit()` pipeline: build `FeedbackEvent` -> correlate -> resolve provenance
(best-effort) -> persist -> transition to `RECEIVED` -> notify subscribers ->
route to handlers (best-effort, isolated per handler).

## Lifecycle

Happy path:

```text
RECEIVED -> ACKNOWLEDGED -> HANDLED -> RESOLVED
```

Terminal alternatives: `REJECTED`, `CANCELLED`, `EXPIRED`.

Rules:

- `resolve()` requires the event to already be `HANDLED`.
- `ACKNOWLEDGED -> RESOLVED` is illegal.
- Same-state transitions (`HANDLED -> HANDLED`, etc.) are legal no-ops, which
  is what makes concurrent retries safe.

Manager helper methods: `acknowledge()`, `mark_handled()`, `resolve()`,
`reject()`, `cancel()`, `expire()`. See the
[Lifecycle model](https://feedback-manager.readthedocs.io/en/latest/architecture/LIFECYCLE/)
for the exact transition table.

## Domain model

Core types live in
[`src/feedback_manager/core/`](https://github.com/smuniharish/feedback-manager/tree/master/src/feedback_manager/core):

- `FeedbackSource` (open string enum): `human`, `tool`, `generation`,
  `evaluator`, `system`, plus custom values.
- `FeedbackCategory` (open string enum): `correction`, `approval`,
  `rejection`, `timeout`, `quality`, `interruption`, plus custom values.
- `FeedbackTarget` / `FeedbackTargetType`: what the feedback is about (graph,
  run, node, tool call, generation, etc.).
- `FeedbackEvent`: the persisted record, including `status`, `payload`,
  `metadata`, and `provenance`.
- `ExecutionContext` / `CorrelationContext`: correlate feedback to run,
  thread, checkpoint, node, tool call, or generation identifiers.

`FeedbackSource`, `FeedbackCategory`, and `FeedbackTargetType` are
intentionally open: prefer documented members, but a custom string value does
not require modifying the package. See
[Sources](https://feedback-manager.readthedocs.io/en/latest/concepts/sources/),
[Categories](https://feedback-manager.readthedocs.io/en/latest/concepts/categories/),
and
[Targets](https://feedback-manager.readthedocs.io/en/latest/concepts/targets/).

## Correlation and provenance

Correlation links a `FeedbackEvent` to the execution it came from; see the
[Correlation model](https://feedback-manager.readthedocs.io/en/latest/architecture/CORRELATION_MODEL/).

Provenance answers "which execution produced the thing this feedback is
about?" `feedback-manager` does not implement its own provenance capture:
`langgraph-xai` is the mandatory and only provenance source, so provenance is
**not** a pluggable extension point. Pass a `langgraph_xai.XAIRuntime` as
`xai_runtime=...` and `FeedbackManager` wires up `XAIProvenanceAdapter`
automatically; `provenance_adapter=...` exists only for tests or advanced
call sites that already hold a constructed adapter (passing both raises
`ValueError`). If no matching execution context is available, `provenance`
stays `None`; submission still succeeds unless the failure policy says
otherwise. See the
[Provenance model](https://feedback-manager.readthedocs.io/en/latest/architecture/PROVENANCE_MODEL/)
and [Provenance concept](https://feedback-manager.readthedocs.io/en/latest/concepts/provenance/).

## Routing and handling

[Routing](https://feedback-manager.readthedocs.io/en/latest/architecture/ROUTING_MODEL/)
(see also the
[routing concept](https://feedback-manager.readthedocs.io/en/latest/concepts/routing/))
selects which handlers should process an event; it does not invoke them.
`FeedbackManager` invokes the selected handlers and applies failure
isolation.
[Handlers](https://feedback-manager.readthedocs.io/en/latest/concepts/handlers/)
are where application side effects live, implementing the `FeedbackHandler`
contract's async `handle()` method.

## Failure isolation and concurrency

Defaults (see the
[Failure model](https://feedback-manager.readthedocs.io/en/latest/architecture/FAILURE_MODEL/)
and
[Failure isolation](https://feedback-manager.readthedocs.io/en/latest/reliability/failure-isolation/)):

- store failures are blocking;
- routing, handler, subscriber, and provenance failures are best-effort.

Concurrency (see the
[Concurrency model](https://feedback-manager.readthedocs.io/en/latest/architecture/CONCURRENCY_MODEL/)
and [Concurrency](https://feedback-manager.readthedocs.io/en/latest/reliability/concurrency/)):
`InMemoryFeedbackStore` uses an `asyncio.Lock`; submission dedup and
same-state lifecycle transitions are safe under concurrent retries; multiple
`FeedbackManager` instances remain independent of each other.

## Pull and push consumption

Pull: `manager.get(feedback_id)`, `manager.query(FeedbackQuery(...))`,
`manager.list()`.

Push: `manager.subscribe(async_subscriber)` returns a `Subscription` with
`cancel()`/`active`; `manager.stream(query=None)` yields events as an async
iterator. Subscriber failures are isolated by the failure policy. See
[Subscriptions](https://feedback-manager.readthedocs.io/en/latest/concepts/subscriptions/).
