"""The central domain object: :class:`FeedbackEvent`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from feedback_manager.core.categories import FeedbackCategory
from feedback_manager.core.context import CorrelationContext, ExecutionContext
from feedback_manager.core.provenance import FeedbackProvenanceReference
from feedback_manager.core.sources import FeedbackSource
from feedback_manager.core.status import FeedbackStatus
from feedback_manager.core.targets import FeedbackTarget


class FeedbackEvent(BaseModel):
    """A single, immutable-by-convention unit of feedback.

    Only ``status``, ``updated_at``, ``correlation``, and ``provenance`` are
    ever changed after creation, and only through
    :meth:`with_status`/:meth:`with_correlation`/:meth:`with_provenance`,
    which return a *new* instance -- callers never mutate a ``FeedbackEvent``
    in place, which keeps concurrent handling and observability simple.
    """

    model_config = ConfigDict(frozen=True)

    feedback_id: UUID = Field(default_factory=uuid4)
    idempotency_key: str | None = None

    source: FeedbackSource
    category: FeedbackCategory
    feedback_type: str | None = None
    target: FeedbackTarget

    payload: dict[str, Any] = Field(default_factory=dict)
    execution_context: ExecutionContext | None = None
    correlation: CorrelationContext | None = None
    provenance: FeedbackProvenanceReference | None = None

    status: FeedbackStatus = FeedbackStatus.CREATED

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at", "updated_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value

    def with_status(self, status: FeedbackStatus) -> FeedbackEvent:
        """Return a copy of this event with a new lifecycle status."""
        return self.model_copy(update={"status": status, "updated_at": datetime.now(UTC)})

    def with_correlation(self, correlation: CorrelationContext) -> FeedbackEvent:
        """Return a copy of this event with correlation information attached."""
        return self.model_copy(update={"correlation": correlation, "updated_at": datetime.now(UTC)})

    def with_provenance(self, provenance: FeedbackProvenanceReference) -> FeedbackEvent:
        """Return a copy of this event with provenance information attached."""
        return self.model_copy(update={"provenance": provenance, "updated_at": datetime.now(UTC)})


__all__ = ["FeedbackEvent"]
