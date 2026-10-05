"""The central domain object: `FeedbackEvent`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from feedback_manager.core._types import Identifier, JsonObject
from feedback_manager.core.categories import FeedbackCategory
from feedback_manager.core.context import ExecutionContext
from feedback_manager.core.provenance import FeedbackProvenanceReference
from feedback_manager.core.sources import FeedbackSource
from feedback_manager.core.status import FeedbackStatus
from feedback_manager.core.targets import FeedbackTarget

_TICK = timedelta(microseconds=1)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class FeedbackEvent(BaseModel):
    """One immutable piece of feedback.

    Events are frozen. `FeedbackManager` and stores derive updated copies;
    nothing changes an event in place, which keeps concurrent handling simple.

    Attributes:
        feedback_id: The event's unique ID.
        idempotency_key: Deduplicates submissions: submitting again with the
            same key returns the original event instead of a new one.
        source: Who or what produced the feedback.
        category: What kind of feedback it is.
        feedback_type: An optional, application-defined subtype, such as
            ``"tool_error"``.
        target: What the feedback is about.
        payload: The feedback content, JSON-compatible.
        metadata: Application-defined, JSON-compatible details.
        execution_context: The execution the target belongs to.
        correlation_id: Groups related feedback; assigned at submission by the
            `FeedbackCorrelator`.
        provenance: The ``langgraph-xai`` records the feedback refers to.
        status: The lifecycle status.
        resolution: How the feedback was closed, set by
            `FeedbackManager.resolve`, `reject`, and `cancel`.
        created_at: When the event was created (timezone-aware).
        updated_at: When the event last changed (timezone-aware). Each change
            sets a later value than the one before, even when the clock has
            not advanced, so it orders the changes of an event.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    feedback_id: UUID = Field(default_factory=uuid4)
    idempotency_key: Identifier | None = None
    source: FeedbackSource
    category: FeedbackCategory
    feedback_type: Identifier | None = None
    target: FeedbackTarget
    payload: JsonObject = Field(default_factory=dict)
    metadata: JsonObject = Field(default_factory=dict)
    execution_context: ExecutionContext | None = None
    correlation_id: Identifier | None = None
    provenance: FeedbackProvenanceReference | None = None
    status: FeedbackStatus = FeedbackStatus.CREATED
    resolution: JsonObject | None = None
    created_at: AwareDatetime = Field(default_factory=_utc_now)
    updated_at: AwareDatetime = Field(default_factory=_utc_now)

    def with_status(
        self, status: FeedbackStatus, *, resolution: JsonObject | None = None
    ) -> FeedbackEvent:
        """Return a copy moved to ``status``, with ``updated_at`` set to now.

        ``updated_at`` always moves forward: if the clock has not advanced past
        the current value, it is set one microsecond later.

        Args:
            status: The new status. Lifecycle rules are not checked here; use
                `validate_transition` first.
            resolution: Recorded as the copy's ``resolution`` when given; the
                existing resolution is kept otherwise.
        """
        update: dict[str, object] = {
            "status": status,
            "updated_at": max(_utc_now(), self.updated_at + _TICK),
        }
        if resolution is not None:
            update["resolution"] = resolution
        return self.model_copy(update=update)


__all__ = ["FeedbackEvent"]
