# Example: overriding every default

`FeedbackManager()` works out of the box with zero arguments -- every
dependency defaults to an in-memory/no-op implementation. This example
shows how to override each one with a minimal real implementation, and
proves each override actually took effect.

Each override is a small, self-contained class implementing the
corresponding `ABC`/`Protocol` (`FeedbackStore`, `FeedbackRouter`,
`FeedbackCorrelator`, `FeedbackLifecyclePolicy`, `FeedbackPolicy`,
`ObservabilitySink`) -- see
[Extensibility model](../architecture/EXTENSIBILITY_MODEL.md) for the full
contract list.

Full source, embedded directly from `examples/09_override_defaults.py`:

```python title="examples/09_override_defaults.py"
--8<-- "examples/09_override_defaults.py"
```

## Real run, real proof each override took effect

```console
$ uv run python examples/09_override_defaults.py
== store override ==
AuditedStore.calls: ['create(5ccf0455-500f-4aaf-8bae-0680bc490e52)']

== router + handler override ==
AuditHandler.handled: [UUID('5ccf0455-500f-4aaf-8bae-0680bc490e52')] (expected: [5ccf0455-500f-4aaf-8bae-0680bc490e52])

== correlator override ==
correlation_id: override-demo-correlation

== xai_runtime override (real langgraph-xai provenance) ==
provenance: execution_id='21bf607d-b2bc-47fb-b3df-81a76f22261a' decision_id=None evidence_ids=() human_interaction_id=None tool_execution_id=None summary='execution status=running, nodes=0' metadata={'node_count': 0, 'tool_count': 0, 'human_interaction_count': 0, 'status': 'running'}

== redaction_policy override ==
payload persisted (email redacted): {'comment': 'great answer', 'email': '***redacted***'}

== lifecycle_policy override ==
Blocked as expected (no metadata['reviewer']): cannot resolve 5ccf0455-500f-4aaf-8bae-0680bc490e52: metadata['reviewer'] is required

== failure_policy override (routing made BLOCKING) ==
Propagated as expected (default would have swallowed this): RuntimeError('router is broken')

== observability_sink override ==
Collected 5 observability events:
  - feedback.created feedback_id=5ccf0455-500f-4aaf-8bae-0680bc490e52
  - feedback.received feedback_id=5ccf0455-500f-4aaf-8bae-0680bc490e52
  - feedback.routed feedback_id=5ccf0455-500f-4aaf-8bae-0680bc490e52
  - feedback.acknowledged feedback_id=5ccf0455-500f-4aaf-8bae-0680bc490e52
  - feedback.handled feedback_id=5ccf0455-500f-4aaf-8bae-0680bc490e52
```

Each section proves the override is live: the custom store's call log is non-empty, the custom handler actually received the event, the correlation ID matches the custom correlator's fixed label, provenance is populated (only possible because a real `langgraph_xai.XAIRuntime` was supplied), the email was redacted before persistence, the custom lifecycle rule genuinely blocked an illegal-by-policy transition, the custom failure policy let a broken router's exception propagate instead of being swallowed, and the custom sink collected the exact events the default `LoggingObservabilitySink` would otherwise have only logged.
