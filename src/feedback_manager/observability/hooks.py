"""Feedback-specific observability events and sinks.

feedback-manager is not a tracing platform. It emits a small set of typed
events to an `ObservabilitySink`; implement one to forward them to
OpenTelemetry, LangSmith, Grafana, or any other backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from feedback_manager import _logging

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

FEEDBACK_RECEIVED = "feedback.received"
"""New feedback was accepted and stored."""
FEEDBACK_ROUTED = "feedback.routed"
"""Received feedback was routed to one or more handlers."""
FEEDBACK_ACKNOWLEDGED = "feedback.acknowledged"
"""Feedback moved to ``ACKNOWLEDGED``."""
FEEDBACK_HANDLED = "feedback.handled"
"""Feedback moved to ``HANDLED``."""
FEEDBACK_RESOLVED = "feedback.resolved"
"""Feedback moved to ``RESOLVED``."""
FEEDBACK_REJECTED = "feedback.rejected"
"""Feedback moved to ``REJECTED``."""
FEEDBACK_CANCELLED = "feedback.cancelled"
"""Feedback moved to ``CANCELLED``."""
FEEDBACK_EXPIRED = "feedback.expired"
"""Feedback moved to ``EXPIRED``."""
FEEDBACK_FAILED = "feedback.failed"
"""A processing stage failed; ``attributes["stage"]`` names it."""


@dataclass(frozen=True, slots=True)
class ObservabilityEvent:
    """One observability event emitted by `FeedbackManager`.

    Attributes:
        name: The event name, one of the ``FEEDBACK_*`` constants.
        feedback_id: The feedback event it concerns.
        occurred_at: When it happened (UTC).
        attributes: Details: ``source``, ``category``, ``target_type``, and
            ``status`` always; ``handler_count`` and ``handled_count`` for
            ``feedback.routed``; ``stage``, ``error_type``, and ``error`` for
            ``feedback.failed``.
    """

    name: str
    feedback_id: UUID
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    attributes: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class ObservabilitySink(Protocol):
    """Receives `ObservabilityEvent` instances.

    ``emit`` is called inline on the feedback path, so it must return quickly:
    hand slow work, such as network calls, to a background worker. Exceptions
    raised by a sink are logged and never interrupt feedback processing.
    """

    def emit(self, event: ObservabilityEvent) -> None:
        """Receive one event."""
        ...


class LoggingObservabilitySink:
    """The default sink: one structured log record per event.

    ``feedback.failed`` is logged at ``WARNING`` and every other event at ``INFO``.
    """

    def __init__(self, logger_name: str = "feedback_manager.observability") -> None:
        self._logger = _logging.get_logger(logger_name)

    def emit(self, event: ObservabilityEvent) -> None:
        """Log ``event`` with its attributes."""
        log = self._logger.warning if event.name == FEEDBACK_FAILED else self._logger.info
        log(
            event.name,
            feedback_id=str(event.feedback_id),
            occurred_at=event.occurred_at.isoformat(),
            **event.attributes,
        )


class NoOpObservabilitySink:
    """A sink that discards every event."""

    def emit(self, event: ObservabilityEvent) -> None:
        """Discard ``event``."""


__all__ = [
    "FEEDBACK_ACKNOWLEDGED",
    "FEEDBACK_CANCELLED",
    "FEEDBACK_EXPIRED",
    "FEEDBACK_FAILED",
    "FEEDBACK_HANDLED",
    "FEEDBACK_RECEIVED",
    "FEEDBACK_REJECTED",
    "FEEDBACK_RESOLVED",
    "FEEDBACK_ROUTED",
    "LoggingObservabilitySink",
    "NoOpObservabilitySink",
    "ObservabilityEvent",
    "ObservabilitySink",
]
