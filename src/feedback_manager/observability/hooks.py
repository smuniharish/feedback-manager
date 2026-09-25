"""Feedback-specific observability hooks.

FeedbackManager does not implement a tracing platform. It emits small,
typed events (Section 32 of the spec) to a pluggable sink; wire up
OpenTelemetry/LangSmith/Langfuse/etc. by implementing
:class:`ObservabilitySink` in application code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

logger = logging.getLogger("feedback_manager.observability")

FEEDBACK_CREATED = "feedback.created"
FEEDBACK_RECEIVED = "feedback.received"
FEEDBACK_ROUTED = "feedback.routed"
FEEDBACK_ACKNOWLEDGED = "feedback.acknowledged"
FEEDBACK_HANDLED = "feedback.handled"
FEEDBACK_RESOLVED = "feedback.resolved"
FEEDBACK_FAILED = "feedback.failed"


@dataclass(frozen=True, slots=True)
class ObservabilityEvent:
    """A single observability event emitted by :class:`FeedbackManager`."""

    name: str
    feedback_id: UUID
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    attributes: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ObservabilitySink(Protocol):
    """Receives :class:`ObservabilityEvent` instances."""

    def emit(self, event: ObservabilityEvent) -> None: ...


class LoggingObservabilitySink:
    """Default sink: structured logging, no external dependency required."""

    def __init__(self, logger_name: str = "feedback_manager.observability") -> None:
        self._logger = logging.getLogger(logger_name)

    def emit(self, event: ObservabilityEvent) -> None:
        self._logger.info(
            "%s feedback_id=%s attributes=%s", event.name, event.feedback_id, event.attributes
        )


class NoOpObservabilitySink:
    """A sink that discards every event -- useful for tests."""

    def emit(self, event: ObservabilityEvent) -> None:
        return None


__all__ = [
    "FEEDBACK_ACKNOWLEDGED",
    "FEEDBACK_CREATED",
    "FEEDBACK_FAILED",
    "FEEDBACK_HANDLED",
    "FEEDBACK_RECEIVED",
    "FEEDBACK_RESOLVED",
    "FEEDBACK_ROUTED",
    "LoggingObservabilitySink",
    "NoOpObservabilitySink",
    "ObservabilityEvent",
    "ObservabilitySink",
]
