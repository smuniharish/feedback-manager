"""Abstract persistence contract for feedback events."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from uuid import UUID

from feedback_manager.core.events import FeedbackEvent
from feedback_manager.core.status import FeedbackStatus


class FeedbackQuery:
    """Filter criteria for :meth:`FeedbackStore.query`.

    All fields are optional; an empty query matches every stored event.
    Kept as a plain, framework-independent container so custom stores can
    translate it into whatever native query mechanism they use (SQL,
    document filters, ...).
    """

    __slots__ = (
        "category",
        "correlation_id",
        "limit",
        "source",
        "status",
        "target_id",
        "target_type",
    )

    def __init__(
        self,
        *,
        source: str | None = None,
        category: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        status: FeedbackStatus | None = None,
        correlation_id: str | None = None,
        limit: int | None = None,
    ) -> None:
        self.source = source
        self.category = category
        self.target_type = target_type
        self.target_id = target_id
        self.status = status
        self.correlation_id = correlation_id
        self.limit = limit

    def matches(self, event: FeedbackEvent) -> bool:
        """Return whether ``event`` satisfies every set filter field."""
        if self.source is not None and event.source != self.source:
            return False
        if self.category is not None and event.category != self.category:
            return False
        if self.target_type is not None and event.target.type != self.target_type:
            return False
        if self.target_id is not None and event.target.id != self.target_id:
            return False
        if self.status is not None and event.status != self.status:
            return False
        if self.correlation_id is not None:
            correlation = event.correlation
            if correlation is None or correlation.correlation_id != self.correlation_id:
                return False
        return True


class FeedbackStore(ABC):
    """Abstract persistence contract for :class:`FeedbackEvent` instances.

    Implementations must be safe under concurrent use: ``create``,
    ``update``/``transition``, and ``get`` may be called concurrently for
    the same or different ``feedback_id`` values from multiple asyncio
    tasks. ``create`` must be idempotent with respect to
    ``idempotency_key`` -- calling it twice with the same key must return
    the original event rather than creating a duplicate.
    """

    @abstractmethod
    async def create(self, feedback: FeedbackEvent) -> FeedbackEvent:
        """Persist a new feedback event, honoring ``idempotency_key`` dedup."""

    @abstractmethod
    async def get(self, feedback_id: UUID) -> FeedbackEvent | None:
        """Return the event for ``feedback_id``, or ``None`` if unknown."""

    @abstractmethod
    async def update(self, feedback: FeedbackEvent) -> FeedbackEvent:
        """Persist an updated version of an existing feedback event."""

    @abstractmethod
    async def transition(self, feedback_id: UUID, status: FeedbackStatus) -> FeedbackEvent:
        """Validate and persist a lifecycle transition, returning the new event."""

    @abstractmethod
    async def query(self, query: FeedbackQuery) -> Sequence[FeedbackEvent]:
        """Return every stored event matching ``query``."""

    @abstractmethod
    async def list(self) -> Sequence[FeedbackEvent]:
        """Return every stored event, in creation order."""


__all__ = ["FeedbackQuery", "FeedbackStore"]
