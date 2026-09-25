# Example: overriding every default

Source file: `examples/09_override_defaults.py`

`FeedbackManager()` works out of the box with zero arguments -- every dependency defaults to an in-memory/no-op implementation. This example shows how to override each one with a minimal real implementation, and proves each override actually took effect.

Key pattern:

```python
manager = FeedbackManager(
    store=AuditedStore(),                      # persistence
    router=RouteEverythingToAudit(handler),    # routing
    correlator=LabelingCorrelator(),           # correlation
    xai_runtime=XAIRuntime(...),               # provenance (langgraph-xai)
    lifecycle_policy=RequireReviewerToResolve(),  # business rules on transitions
    redaction_policy=RedactEmails(),           # payload redaction
    failure_policy=FailurePolicy(modes={FeedbackStage.ROUTING: FailureMode.BLOCKING}),
    observability_sink=ListObservabilitySink(),
)
```

Each override is a small, self-contained class implementing the corresponding `ABC`/`Protocol` (`FeedbackStore`, `FeedbackRouter`, `FeedbackCorrelator`, `FeedbackLifecyclePolicy`, `FeedbackPolicy`, `ObservabilitySink`) -- see [Extensibility model](../architecture/EXTENSIBILITY_MODEL.md) for the full contract list.

## Real run, real proof each override took effect

```console
$ uv run python examples/09_override_defaults.py
== store override ==
AuditedStore.calls: ['create(7cd79e45-ec1a-4bd2-93c4-e212d1927bcd)']

== router + handler override ==
AuditHandler.handled: [UUID('7cd79e45-ec1a-4bd2-93c4-e212d1927bcd')] (expected: [7cd79e45-ec1a-4bd2-93c4-e212d1927bcd])

== correlator override ==
correlation_id: override-demo-correlation

== xai_runtime override (real langgraph-xai provenance) ==
provenance: execution_id='1eef4a12-daca-413f-a7a7-8f8fe594f52a' decision_id=None ... summary='execution status=running, nodes=0'

== redaction_policy override ==
payload persisted (email redacted): {'comment': 'great answer', 'email': '***redacted***'}

== lifecycle_policy override ==
Blocked as expected (no metadata['reviewer']): cannot resolve 7cd79e45-...: metadata['reviewer'] is required

== failure_policy override (routing made BLOCKING) ==
Propagated as expected (default would have swallowed this): RuntimeError('router is broken')

== observability_sink override ==
Collected 5 observability events:
  - feedback.created feedback_id=7cd79e45-ec1a-4bd2-93c4-e212d1927bcd
  - feedback.received feedback_id=7cd79e45-ec1a-4bd2-93c4-e212d1927bcd
  - feedback.routed feedback_id=7cd79e45-ec1a-4bd2-93c4-e212d1927bcd
  - feedback.acknowledged feedback_id=7cd79e45-ec1a-4bd2-93c4-e212d1927bcd
  - feedback.handled feedback_id=7cd79e45-ec1a-4bd2-93c4-e212d1927bcd
```

Each section proves the override is live: the custom store's call log is non-empty, the custom handler actually received the event, the correlation ID matches the custom correlator's fixed label, provenance is populated (only possible because a real `langgraph_xai.XAIRuntime` was supplied), the email was redacted before persistence, the custom lifecycle rule genuinely blocked an illegal-by-policy transition, the custom failure policy let a broken router's exception propagate instead of being swallowed, and the custom sink collected the exact events the default `LoggingObservabilitySink` would otherwise have only logged.
