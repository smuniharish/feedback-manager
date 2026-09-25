# Troubleshooting

Source of truth: [`src/feedback_manager/errors/exceptions.py`](../../../../src/feedback_manager/errors/exceptions.py),
[`docs/architecture/FAILURE_MODEL.md`](../../../../docs/architecture/FAILURE_MODEL.md),
[`docs/reliability/`](../../../../docs/reliability), and
[`docs/faq/index.md`](../../../../docs/faq/index.md). Reproduce first with the
exact source/category/target/payload/execution_context in use; do not guess
at a fix from the exception name alone.

## Exception hierarchy

Every exception derives from `FeedbackManagerError` (carries `feedback_id`
and free-form `context`), so application code can catch the whole domain
with one `except FeedbackManagerError` while still discriminating on the
specific failure:

| Exception | Raised when |
| --- | --- |
| `FeedbackValidationError` | A feedback event fails domain validation. |
| `FeedbackNotFoundError` | A feedback event cannot be found by id. |
| `FeedbackLifecycleError` | An illegal or unsafe lifecycle transition is attempted (also carries `current_status`/`requested_status`). |
| `FeedbackRoutingError` | Routing a feedback event to handlers fails. |
| `FeedbackHandlerError` | A feedback handler fails to process an event. |
| `FeedbackStoreError` | The underlying `FeedbackStore` fails an operation. |
| `FeedbackCorrelationError` | Correlation/provenance resolution fails. |
| `FeedbackSerializationError` | Serializing or deserializing a feedback event fails. |
| `FeedbackConfigurationError` | `FeedbackManager` (or a dependency) is misconfigured. |

Do not add a blanket `except Exception` around `submit()`/lifecycle calls to
silence one of these; catch the specific exception the situation calls for.

## Common failure scenarios

### "resolve() raised FeedbackLifecycleError"

Check `current_status`/`requested_status` on the exception. `resolve()`
requires the event to already be `HANDLED`; `ACKNOWLEDGED -> RESOLVED` is
illegal. Call `mark_handled()` first, or use `reject()`/`cancel()`/`expire()`
if a terminal-but-not-resolved outcome is what's actually needed.

### "submit() with the same idempotency_key returns the same event unexpectedly"

This is intended behavior, not a bug: `FeedbackStore.create()` must
deduplicate by `idempotency_key`, and `submit()` returns the existing event
rather than recreating or re-transitioning it. If a genuinely new event is
needed, use a new `idempotency_key` (or omit it).

### "A handler/router/subscriber raised but submit() still succeeded"

Expected: routing, handler, subscriber, and provenance failures are
best-effort/isolated by `FailurePolicy` by default — only store failures are
blocking. Inspect `ObservabilityEvent`s (e.g. `feedback.failed`) via the
configured `observability_sink`, or pass a custom `failure_policy` if a
specific `FeedbackStage` should actually be blocking for this application.

### "provenance is always None"

Provenance is resolved via `langgraph-xai` only, and only for executions the
runtime actually instrumented:

1. Confirm `xai_runtime=...` (or a pre-built `provenance_adapter=...`, not
   both — passing both raises `ValueError`) was passed to `FeedbackManager`.
2. Confirm the same `XAIRuntime` instruments the graph/run being submitted
   for, and that the execution occurred before/at submission time.
3. Remember provenance resolution is best-effort: a missing match leaves
   `FeedbackEvent.provenance` as `None` without failing `submit()`.

### "Custom FeedbackStore doesn't reject illegal transitions"

The store's `transition()` must call the package's public
`validate_transition()` helper before persisting — this is a contract
requirement, not something `FeedbackManager` enforces on the store's behalf.
Add that call rather than duplicating the transition table in application
code.

### "Concurrent submissions/transitions behave inconsistently"

`InMemoryFeedbackStore` uses an `asyncio.Lock` and is safe under concurrent
use with idempotent submission and idempotent same-state transitions. A
custom store must provide equivalent guarantees (see
`docs/architecture/CONCURRENCY_MODEL.md` and
`docs/reliability/concurrency.md`); this is a common gap when replacing the
default store.

### "Feedback never expires"

`FeedbackManager` deliberately has no background scheduler. `RetentionPolicy`
is a pure predicate (`is_expired()`); an application-owned periodic task must
call it and then invoke `manager.expire(feedback_id)` itself.

## Debugging checklist

1. Reproduce with the exact `source`, `category`, `target`, `payload`, and
   `execution_context` from the failing call.
2. Identify which exception (if any) was raised and read its `feedback_id`
   and `context`.
3. Check whether the failure is in a blocking stage (store) or a
   best-effort stage (routing/handler/subscriber/provenance) per
   `FailurePolicy` — a best-effort failure that doesn't raise is not a bug.
4. Check the emitted `ObservabilityEvent`s through the configured
   `observability_sink` for the sequence of what actually happened.
5. If the failure is inside a custom store/router/handler/policy, re-check
   it against the contract requirements in
   [`references/extensibility.md`](extensibility.md) before assuming a
   package bug.
6. Only after the above, consult
   [`docs/faq/index.md`](../../../../docs/faq/index.md) and the architecture
   docs under [`docs/architecture/`](../../../../docs/architecture) for the
   authoritative behavior, and the test suite under
   [`tests/`](../../../../tests) for executable evidence of expected behavior.
