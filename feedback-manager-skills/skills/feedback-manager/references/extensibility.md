# Extensibility

Source of truth: [`docs/architecture/EXTENSIBILITY_MODEL.md`](../../../../docs/architecture/EXTENSIBILITY_MODEL.md),
[`docs/advanced/`](../../../../docs/advanced),
[`src/feedback_manager/contracts/`](../../../../src/feedback_manager/contracts),
and [`examples/09_override_defaults.py`](../../../../examples/09_override_defaults.py).
Every extension point is an injected dependency on `FeedbackManager`'s
constructor; never subclass or monkeypatch `FeedbackManager` to add one.

## Store — `FeedbackStore`

Implement when durable persistence is needed; the bundled
`InMemoryFeedbackStore` ([`storage/memory.py`](../../../../src/feedback_manager/storage/memory.py))
is a reference implementation, not a production adapter.

Required async methods: `create()`, `get()`, `update()`, `transition()`,
`query()`, `list()`.

Behavioral requirements from the contract docstring:

- safe under concurrent use;
- `create()` must honor `idempotency_key` (return the existing event instead
  of creating a duplicate);
- `transition()` must call the package's public `validate_transition()`
  helper before persisting the new status, so illegal transitions still
  raise `FeedbackLifecycleError` regardless of backend.

Store failures are blocking by default (see `references/troubleshooting.md`).
See [`docs/concepts/stores.md`](../../../../docs/concepts/stores.md) and
[`examples/postgres_feedback_store.py`](../../../../examples/postgres_feedback_store.py)
for a real Postgres-backed implementation.

## Router — `FeedbackRouter`

Implement when predicate-based rules in `DefaultFeedbackRouter` are not
enough. `route()` must be side-effect free: it selects handlers, it does not
call them.

```python
class SeverityRouter(FeedbackRouter):
    async def route(self, feedback: FeedbackEvent) -> Sequence[FeedbackHandler]:
        ...
```

For the bundled predicate rules, use `RoutingRule`, `by_source()`,
`by_category()`, `by_target_type()`, `all_of()`, `any_of()` from
`feedback_manager.routing` with `DefaultFeedbackRouter`, rather than writing a
full custom router when a predicate combination already covers the case. See
[`docs/concepts/routing.md`](../../../../docs/concepts/routing.md).

## Handler — `FeedbackHandler`

Implement for application-specific side effects (create a ticket, notify an
operator, enqueue downstream work). Contract:

```python
class FeedbackHandler(ABC):
    async def handle(
        self, feedback: FeedbackEvent, context: FeedbackContext
    ) -> FeedbackHandlerResult: ...
```

The bundled `AuditFeedbackHandler` ([`handlers/audit.py`](../../../../src/feedback_manager/handlers/audit.py))
only does structured logging. Handler failures are isolated by
`FailurePolicy` (stage `HANDLER`) unless the application explicitly opts into
blocking behavior. See [`docs/concepts/handlers.md`](../../../../docs/concepts/handlers.md).

## Correlator — `FeedbackCorrelator`

Implement only if the default correlation strategy
(`DefaultFeedbackCorrelator`) does not fit a bespoke execution-context shape.
Most applications should reach for `ExecutionContext` /
`execution_context_from_config()` first. See
[`docs/architecture/CORRELATION_MODEL.md`](../../../../docs/architecture/CORRELATION_MODEL.md).

## Policies

- `lifecycle_policy` (`FeedbackLifecyclePolicy`): hook into lifecycle
  transitions beyond the built-in rules.
- `redaction_policy` (`FeedbackPolicy`): redact or transform payload/metadata,
  e.g. for PII.
- `failure_policy` (`FailurePolicy`, [`policies/failure.py`](../../../../src/feedback_manager/policies/failure.py)):
  configure which `FeedbackStage` values (store, routing, handler, subscriber,
  provenance) are blocking vs. best-effort.
- `RetentionPolicy` ([`policies/retention.py`](../../../../src/feedback_manager/policies/retention.py)):
  a pure predicate (`is_expired()`) for whether a non-terminal event should be
  transitioned to `EXPIRED`. `FeedbackManager` does not run a background
  scheduler by design — an application-owned periodic task must call
  `manager.expire(...)` itself when `RetentionPolicy.is_expired()` is true.

## Observability — `ObservabilitySink`

`FeedbackManager` does not implement a tracing platform; it emits small,
typed `ObservabilityEvent` instances
(`feedback.created`, `feedback.received`, `feedback.routed`,
`feedback.acknowledged`, `feedback.handled`, `feedback.resolved`,
`feedback.failed`) to a pluggable sink. Implement `ObservabilitySink.emit()`
to wire up OpenTelemetry, LangSmith, Langfuse, Grafana, or another platform,
instead of adding tracing calls inside application handlers. The default
`LoggingObservabilitySink` uses `structlog`; `NoOpObservabilitySink` exists
for tests. See [`examples/11_grafana_dashboard.py`](../../../../examples/11_grafana_dashboard.py).

## Open enum values

`FeedbackSource`, `FeedbackCategory`, and `FeedbackTargetType` are open
string types — construct a custom value (e.g. `FeedbackSource("mcp_server")`)
directly; do not subclass them or patch the package to add an
application-specific value. See
[`docs/advanced/custom-source-category.md`](../../../../docs/advanced/custom-source-category.md).
