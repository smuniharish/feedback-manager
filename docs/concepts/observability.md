# Observability

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
