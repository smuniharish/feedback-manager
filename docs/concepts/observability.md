# Observability

## Optional OpenTelemetry sink

Install `feedback-manager[otel]` and pass
`feedback_manager.observability.otel.OpenTelemetryObservabilitySink` as the
manager's `observability_sink`. Configure OpenTelemetry tracer/meter providers
and exporters in the host; the library does not install global providers or
start network exporters. Spans inherit the current host/agxai trace context.

The sink emits feedback-event spans and a low-cardinality event counter.
Payloads, exception messages and arbitrary event attributes are excluded. IDs are
opt-in span attributes, never metric labels.

Pass the same sink as `observer=` to `LearningEngine`, `ArtifactIndexer` and a
hybrid/vector/graph retriever to enable operation spans and a duration histogram.
Background jobs inherit the engine's observer. These scopes include learning
batches, jobs, projections, retrieval, embedding calls and projection-database
operations. Operation names and outcome labels are bounded; exception payloads
and stack events are excluded. Context follows the current async task. Custom
observers subclass the framework-neutral `OperationObserver` base class and must
not suppress failures. This is operation-level timing, not instrumentation of
every internal query in an arbitrary provider SDK.

```python
from feedback_manager.learning.engine import LearningEngine
from feedback_manager.observability.otel import OpenTelemetryObservabilitySink
from feedback_manager.storage.artifacts import InMemoryArtifactRepository

telemetry = OpenTelemetryObservabilitySink()
engine = LearningEngine(InMemoryArtifactRepository(), observer=telemetry)
# Pass engine as learning_engine= and telemetry as observability_sink= to the manager.
```

For background learning, inspect `background_learner.stats` for enqueued,
duplicate, skipped, succeeded, learned, retried and dead-lettered counts and
`store.counts()` for current queue depth. Job logs and stored errors use exception
types, not raw feedback text. Alert on quarantined scope, growing dead letters
and repeated reindex failures.

`FeedbackManager` reports what happens to feedback as typed
`ObservabilityEvent` objects sent to an `ObservabilitySink`. The default sink,
`LoggingObservabilitySink`, writes one structured log record per event; replace
it to feed metrics, dashboards, or alerts.

feedback-manager is not a tracing platform. Use LangSmith, OpenTelemetry, or
your tracing tool for traces, and these events for feedback-specific signals.

## Events

| Name | Emitted when | Extra attributes |
|---|---|---|
| `feedback.received` | New feedback was stored. | |
| `feedback.routed` | Handlers ran for new feedback. | `handler_count`, `handled_count` |
| `feedback.acknowledged` | Feedback moved to `ACKNOWLEDGED`. | |
| `feedback.handled` | Feedback moved to `HANDLED`. | |
| `feedback.resolved` | Feedback moved to `RESOLVED`. | |
| `feedback.rejected` | Feedback moved to `REJECTED`. | |
| `feedback.cancelled` | Feedback moved to `CANCELLED`. | |
| `feedback.expired` | Feedback moved to `EXPIRED`. | |
| `feedback.failed` | A processing stage failed. | `stage`, `error_type`, `error` |

Every event carries the `feedback_id`, the time it occurred (`occurred_at`,
UTC), and the attributes `source`, `category`, `target_type`, and `status`. The
names are available as constants, such as
`feedback_manager.observability.FEEDBACK_FAILED`.

`feedback.failed` names the failed `stage`: `correlation`, `provenance`,
`routing`, `handler`, `subscriber`, or `store`. It is emitted whether the
failure was isolated or raised.

## Writing a sink

A sink is any object with an `emit(event)` method:

```python
from collections import Counter

from feedback_manager import FeedbackManager
from feedback_manager.observability import ObservabilityEvent


class CountingSink:
    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()

    def emit(self, event: ObservabilityEvent) -> None:
        self.counts[event.name] += 1


manager = FeedbackManager(observability_sink=CountingSink())
```

`emit` is called inline on the feedback path, so it must return quickly. Hand
slow work, such as network calls, to a background worker, as the
[Grafana example](../examples/production.md#grafana-dashboard) does with its
annotation sink.

A sink that raises never interrupts feedback processing: the manager logs a
warning and continues.

## Bundled sinks

| Sink | Behavior |
|---|---|
| `LoggingObservabilitySink(logger_name="feedback_manager.observability")` | One structured log record per event; `feedback.failed` at `WARNING`, the rest at `INFO`. |
| `NoOpObservabilitySink()` | Discards every event. |

See [logging](../getting-started/configuration.md#logging) for how the records
look and how to route them.
