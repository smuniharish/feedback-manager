"""In-memory reference implementation of :class:`FeedbackStore`.

Suitable for tests, examples, and single-process applications. Production
users are expected to supply their own :class:`FeedbackStore` (backed by a
real database) by implementing the ABC -- this class exists to make the
package usable with zero configuration and to serve as a concurrency-safety
reference implementation.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Sequence
from uuid import UUID

from feedback_manager.contracts.store import FeedbackQuery, FeedbackStore
from feedback_manager.core.events import FeedbackEvent
from feedback_manager.core.lifecycle import validate_transition
from feedback_manager.core.status import FeedbackStatus
from feedback_manager.errors import FeedbackNotFoundError, FeedbackStoreError


class InMemoryFeedbackStore(FeedbackStore):
    """An ``asyncio``-safe, process-local :class:`FeedbackStore`.

    Concurrency model: a single global lock protects the small amount of
    index bookkeeping (id -> event, idempotency key -> id) so that
    concurrent ``create``/``update``/``transition`` calls never interleave
    in a way that corrupts state. This is a deliberately simple design
    (Section 26/28 of the spec) -- it favors correctness over throughput,
    which is appropriate for a reference/testing store.
    """

    def __init__(self) -> None:
        self._events: OrderedDict[UUID, FeedbackEvent] = OrderedDict()
        self._idempotency_index: dict[str, UUID] = {}
        self._lock = asyncio.Lock()

    async def create(self, feedback: FeedbackEvent) -> FeedbackEvent:
        async with self._lock:
            if feedback.idempotency_key is not None:
                existing_id = self._idempotency_index.get(feedback.idempotency_key)
                if existing_id is not None:
                    return self._events[existing_id]
            if feedback.feedback_id in self._events:
                raise FeedbackStoreError(
                    "a feedback event with this id already exists",
                    feedback_id=feedback.feedback_id,
                )
            self._events[feedback.feedback_id] = feedback
            if feedback.idempotency_key is not None:
                self._idempotency_index[feedback.idempotency_key] = feedback.feedback_id
            return feedback

    async def get(self, feedback_id: UUID) -> FeedbackEvent | None:
        async with self._lock:
            return self._events.get(feedback_id)

    async def update(self, feedback: FeedbackEvent) -> FeedbackEvent:
        async with self._lock:
            if feedback.feedback_id not in self._events:
                raise FeedbackNotFoundError(
                    "cannot update a feedback event that was never created",
                    feedback_id=feedback.feedback_id,
                )
            self._events[feedback.feedback_id] = feedback
            return feedback

    async def transition(self, feedback_id: UUID, status: FeedbackStatus) -> FeedbackEvent:
        async with self._lock:
            current = self._events.get(feedback_id)
            if current is None:
                raise FeedbackNotFoundError(
                    "cannot transition an unknown feedback event", feedback_id=feedback_id
                )
            validate_transition(feedback_id, current.status, status)
            updated = current.with_status(status)
            self._events[feedback_id] = updated
            return updated

    async def query(self, query: FeedbackQuery) -> Sequence[FeedbackEvent]:
        async with self._lock:
            matches = [event for event in self._events.values() if query.matches(event)]
        if query.limit is not None:
            matches = matches[: query.limit]
        return matches

    async def list(self) -> Sequence[FeedbackEvent]:
        async with self._lock:
            return list(self._events.values())


__all__ = ["InMemoryFeedbackStore"]
