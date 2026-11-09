"""Optional OpenTelemetry feedback spans and low-cardinality counters.

Applications configure providers and exporters. Importing the core package does
not import OpenTelemetry, install global providers or start network exporters.
"""

from __future__ import annotations

try:
    from opentelemetry import metrics, trace
except ImportError as exc:  # pragma: no cover - optional dependency
    raise ImportError("Install feedback-manager[otel] for OpenTelemetry support") from exc

from contextlib import contextmanager
from time import perf_counter
from typing import TYPE_CHECKING

from feedback_manager.observability.hooks import ObservabilitySink
from feedback_manager.observability.operations import OperationObserver

if TYPE_CHECKING:
    from collections.abc import Iterator
    from contextlib import AbstractContextManager

    from feedback_manager.observability.hooks import ObservabilityEvent

_EVENTS = frozenset(
    {
        "feedback.received",
        "feedback.routed",
        "feedback.acknowledged",
        "feedback.handled",
        "feedback.resolved",
        "feedback.rejected",
        "feedback.cancelled",
        "feedback.expired",
        "feedback.failed",
    }
)
_OPERATIONS = frozenset(
    {
        "learning.batch",
        "learning.job",
        "learning.projection",
        "learning.retrieval",
        "embedding.documents",
        "embedding.query",
        "database.vector.read",
        "database.vector.write",
        "database.graph.read",
        "database.graph.write",
        "database.artifact.read",
        "database.artifact.write",
    }
)


class OpenTelemetryObservabilitySink(ObservabilitySink, OperationObserver):
    """Emit spans and counters without recording feedback contents or exception text.

    Trace correlation follows the host's current context (including agxai spans).
    IDs are opt-in and never metric labels. This library never owns provider
    shutdown; the application must flush and shut down its configured exporters.
    """

    def __init__(
        self,
        *,
        tracer_provider: trace.TracerProvider | None = None,
        meter_provider: metrics.MeterProvider | None = None,
        include_identifiers: bool = False,
    ) -> None:
        self._tracer = trace.get_tracer("feedback_manager", tracer_provider=tracer_provider)
        self._counter = metrics.get_meter(
            "feedback_manager", meter_provider=meter_provider
        ).create_counter("feedback_manager.events", unit="{event}")
        self._include_identifiers = include_identifiers
        self._duration = metrics.get_meter(
            "feedback_manager", meter_provider=meter_provider
        ).create_histogram("feedback_manager.operation.duration", unit="s")

    def observe(self, name: str) -> AbstractContextManager[None]:
        """Time a bounded operation without exporting exception messages or stacks."""
        return self._observe(name)

    @contextmanager
    def _observe(self, name: str) -> Iterator[None]:
        name = name if name in _OPERATIONS else "operation.other"
        started = perf_counter()
        outcome = "ok"
        with self._tracer.start_as_current_span(
            name,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                yield
            except BaseException:
                outcome = "error"
                span.set_status(trace.StatusCode.ERROR)
                raise
            finally:
                self._duration.record(
                    perf_counter() - started,
                    attributes={"operation": name, "outcome": outcome},
                )

    def emit(self, event: ObservabilityEvent) -> None:
        """Record one event with bounded labels and no payload or error contents."""
        name = event.name if event.name in _EVENTS else "feedback.other"
        attributes: dict[str, str | int | bool | float] = {"feedback.event": name}
        self._counter.add(1, attributes=attributes)
        if self._include_identifiers:
            attributes["feedback.id"] = str(event.feedback_id)
        with self._tracer.start_as_current_span(name, attributes=attributes) as span:
            if event.name == "feedback.failed":
                span.set_status(trace.StatusCode.ERROR)


__all__ = ["OpenTelemetryObservabilitySink"]
