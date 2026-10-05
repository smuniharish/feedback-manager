"""The bundled, in-memory `FeedbackStore`."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from feedback_manager.contracts.store import FeedbackQuery, FeedbackStore
from feedback_manager.core.lifecycle import validate_transition
from feedback_manager.errors import (
    FeedbackConflictError,
    FeedbackNotFoundError,
    FeedbackStoreError,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from feedback_manager.core._types import JsonObject
    from feedback_manager.core.events import FeedbackEvent
    from feedback_manager.core.status import FeedbackStatus


class InMemoryFeedbackStore(FeedbackStore):
    """A process-local store for tests, examples, and single-process applications.

    One lock guards the event index and the idempotency index. No critical
    section awaits, so a `threading.Lock` makes the store safe to share across
    tasks, threads, and event loops. Events are kept until the process exits;
    use a database-backed `FeedbackStore` for durable storage.
    """

    def __init__(self) -> None:
        self._events: dict[UUID, FeedbackEvent] = {}
        self._idempotency_index: dict[str, UUID] = {}
        self._lock = threading.Lock()

    async def create(self, feedback: FeedbackEvent) -> FeedbackEvent:
        """Store ``feedback``, or return the event stored with its ``idempotency_key``.

        Raises:
            FeedbackStoreError: If an event with the same ``feedback_id`` exists.
        """
        key = feedback.idempotency_key
        with self._lock:
            if key is not None and (existing := self._idempotency_index.get(key)) is not None:
                return self._events[existing]
            if feedback.feedback_id in self._events:
                raise FeedbackStoreError(
                    "a feedback event with this ID already exists", feedback_id=feedback.feedback_id
                )
            self._events[feedback.feedback_id] = feedback
            if key is not None:
                self._idempotency_index[key] = feedback.feedback_id
            return feedback

    async def get(self, feedback_id: UUID) -> FeedbackEvent | None:
        """Return the event with ``feedback_id``, or ``None``."""
        with self._lock:
            return self._events.get(feedback_id)

    async def transition(
        self,
        feedback_id: UUID,
        status: FeedbackStatus,
        *,
        expected: FeedbackStatus,
        resolution: JsonObject | None = None,
    ) -> FeedbackEvent:
        """Atomically move an event from ``expected`` to ``status``.

        Raises:
            FeedbackNotFoundError: If there is no event with ``feedback_id``.
            FeedbackConflictError: If the stored status is not ``expected``.
            FeedbackLifecycleError: If ``expected -> status`` is not a legal move.
        """
        with self._lock:
            current = self._events.get(feedback_id)
            if current is None:
                raise FeedbackNotFoundError("unknown feedback event", feedback_id=feedback_id)
            if current.status != expected:
                raise FeedbackConflictError(
                    f"feedback is {current.status}, not the expected {expected}",
                    feedback_id=feedback_id,
                    current_status=current.status.value,
                    requested_status=status.value,
                )
            if validate_transition(feedback_id, expected, status).idempotent:
                return current
            updated = current.with_status(status, resolution=resolution)
            self._events[feedback_id] = updated
            return updated

    async def query(self, query: FeedbackQuery) -> Sequence[FeedbackEvent]:
        """Return the events matching ``query``, stopping as soon as ``limit`` is reached."""
        with self._lock:
            if query.idempotency_key is not None:
                # The idempotency index finds the only possible match directly.
                feedback_id = self._idempotency_index.get(query.idempotency_key)
                event = None if feedback_id is None else self._events[feedback_id]
                return [event] if event is not None and query.matches(event) else []
            matches: list[FeedbackEvent] = []
            events = (
                reversed(self._events.values()) if query.newest_first else self._events.values()
            )
            for event in events:
                if query.matches(event):
                    matches.append(event)
                    if len(matches) == query.limit:
                        break
        return matches


__all__ = ["InMemoryFeedbackStore"]
