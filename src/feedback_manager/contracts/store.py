"""The persistence contract and the query object it accepts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from feedback_manager.core.status import FeedbackStatus
from feedback_manager.errors import FeedbackValidationError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    from feedback_manager.core._types import JsonObject
    from feedback_manager.core.events import FeedbackEvent


@dataclass(frozen=True, slots=True, kw_only=True)
class FeedbackQuery:
    """Filters for `FeedbackStore.query`, `FeedbackManager.query`, and `FeedbackManager.stream`.

    Every filter is optional and the set filters must all match; an empty
    query matches every event. Results are in creation order, oldest first.

    Attributes:
        source: Only events from this source.
        category: Only events of this category.
        target_type: Only events whose target has this type.
        target_id: Only events whose target has this ID.
        status: Only events in this status.
        correlation_id: Only events with this correlation ID.
        idempotency_key: Only the event submitted with this idempotency key.
        created_after: Only events created at or after this time (timezone-aware).
        created_before: Only events created before this time (timezone-aware).
        newest_first: Return the newest events first instead of the oldest.
        limit: Return at most this many events (a positive integer).

    Raises:
        FeedbackValidationError: If a status is unknown, a time is naive or
            the time range is empty, or ``limit`` is not a positive integer.
    """

    source: str | None = None
    category: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    status: FeedbackStatus | None = None
    correlation_id: str | None = None
    idempotency_key: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    newest_first: bool = False
    limit: int | None = None

    def __post_init__(self) -> None:
        if self.status is not None:
            try:
                status = FeedbackStatus(self.status)
            except ValueError:
                raise FeedbackValidationError(f"unknown feedback status {self.status!r}") from None
            object.__setattr__(self, "status", status)
        for name in ("created_after", "created_before"):
            value: datetime | None = getattr(self, name)
            if value is not None and value.utcoffset() is None:
                raise FeedbackValidationError(f"{name} must be timezone-aware")
        if (
            self.created_after is not None
            and self.created_before is not None
            and self.created_after >= self.created_before
        ):
            raise FeedbackValidationError("created_after must be earlier than created_before")
        if self.limit is not None and (
            isinstance(self.limit, bool) or not isinstance(self.limit, int) or self.limit < 1
        ):
            raise FeedbackValidationError(f"limit must be a positive integer, not {self.limit!r}")

    def matches(self, event: FeedbackEvent) -> bool:
        """Return whether ``event`` satisfies every set filter (``limit`` and order aside)."""
        return (
            (self.source is None or event.source == self.source)
            and (self.category is None or event.category == self.category)
            and (self.target_type is None or event.target.type == self.target_type)
            and (self.target_id is None or event.target.id == self.target_id)
            and (self.status is None or event.status == self.status)
            and (self.correlation_id is None or event.correlation_id == self.correlation_id)
            and (self.idempotency_key is None or event.idempotency_key == self.idempotency_key)
            and (self.created_after is None or event.created_at >= self.created_after)
            and (self.created_before is None or event.created_at < self.created_before)
        )


class FeedbackStore(ABC):
    """Persists `FeedbackEvent` records.

    `InMemoryFeedbackStore` is the bundled implementation. To keep feedback in
    your own database, subclass this class and pass an instance to
    `FeedbackManager`. Implementations must be safe to call concurrently from
    multiple tasks, threads, and event loops.
    """

    @abstractmethod
    async def create(self, feedback: FeedbackEvent) -> FeedbackEvent:
        """Store a new event, deduplicating by ``idempotency_key``.

        Returns:
            ``feedback``, or the event already stored with the same
            ``idempotency_key``, unchanged.

        Raises:
            FeedbackStoreError: If an event with the same ``feedback_id`` exists.
        """

    @abstractmethod
    async def get(self, feedback_id: UUID) -> FeedbackEvent | None:
        """Return the event with ``feedback_id``, or ``None`` if there is none."""

    @abstractmethod
    async def transition(
        self,
        feedback_id: UUID,
        status: FeedbackStatus,
        *,
        expected: FeedbackStatus,
        resolution: JsonObject | None = None,
    ) -> FeedbackEvent:
        """Atomically move an event from ``expected`` to ``status``.

        The move is a compare-and-set: apply it only if the stored status still
        equals ``expected``, after checking it with `validate_transition`, and
        record ``resolution`` with it when given. The updated event comes from
        `FeedbackEvent.with_status`.

        Returns:
            The updated event, or the stored event unchanged when ``status``
            equals ``expected``.

        Raises:
            FeedbackNotFoundError: If there is no event with ``feedback_id``.
            FeedbackConflictError: If the stored status is not ``expected``.
            FeedbackLifecycleError: If ``expected -> status`` is not a legal move.
        """

    @abstractmethod
    async def query(self, query: FeedbackQuery) -> Sequence[FeedbackEvent]:
        """Return the events matching ``query``.

        Events are in creation order, newest first when ``query.newest_first``
        is set, and at most ``query.limit`` of them. `FeedbackManager.submit`
        queries by ``idempotency_key`` before processing a submission that has
        one, so make that lookup fast, for example with an index.
        """


__all__ = ["FeedbackQuery", "FeedbackStore"]
