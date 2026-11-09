"""A FeedbackStore shaped like one database table, to copy and adapt.

Each ``self._rows`` operation below stands for one database statement; the
comment above it names the statement. Replace them with your driver's calls,
keep the semantics, and certify the result with `test_store_contract.py` or
``python scripts/check_component.py your_module:YourStore``.

Reference table:

    CREATE TABLE feedback_events (
        sequence        BIGSERIAL PRIMARY KEY,         -- creation order
        feedback_id     UUID NOT NULL UNIQUE,
        tenant_id       TEXT,                          -- execution_context.tenant_id
        idempotency_key TEXT,
        source          TEXT NOT NULL,
        category        TEXT NOT NULL,
        target_type     TEXT NOT NULL,
        target_id       TEXT NOT NULL,
        status          TEXT NOT NULL,
        correlation_id  TEXT,
        created_at      TIMESTAMPTZ NOT NULL,
        event           JSONB NOT NULL                 -- FeedbackEvent.model_dump_json()
    );
    CREATE UNIQUE INDEX ON feedback_events (COALESCE(tenant_id, ''), idempotency_key)
        WHERE idempotency_key IS NOT NULL;
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from feedback_manager import (
    FeedbackConflictError,
    FeedbackEvent,
    FeedbackNotFoundError,
    FeedbackStoreError,
    validate_transition,
)
from feedback_manager.contracts import FeedbackQuery, FeedbackStore

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from feedback_manager import FeedbackStatus
    from feedback_manager.core import JsonObject


@dataclass(slots=True)
class _Row:
    sequence: int
    tenant_id: str | None
    idempotency_key: str | None
    event: str  # the JSON column


def _tenant(feedback: FeedbackEvent) -> str | None:
    context = feedback.execution_context
    return None if context is None else context.tenant_id


class TableFeedbackStore(FeedbackStore):
    """Replace the class name, the constructor, and every ``self._rows`` operation."""

    def __init__(self) -> None:
        self._rows: dict[UUID, _Row] = {}  # Replace with a connection pool.
        self._sequence = 0
        self._lock = threading.Lock()  # Replace with database transactions.

    async def create(self, feedback: FeedbackEvent) -> FeedbackEvent:
        tenant = _tenant(feedback)
        key = feedback.idempotency_key
        with self._lock:
            # INSERT ... ON CONFLICT (tenant, idempotency_key) DO NOTHING RETURNING event;
            # when nothing is returned, SELECT the existing row and return it.
            if key is not None:
                for row in self._rows.values():
                    if row.tenant_id == tenant and row.idempotency_key == key:
                        return FeedbackEvent.model_validate_json(row.event)
            # A duplicate feedback_id violates the UNIQUE constraint: raise FeedbackStoreError.
            if feedback.feedback_id in self._rows:
                raise FeedbackStoreError(
                    "a feedback event with this ID already exists", feedback_id=feedback.feedback_id
                )
            self._sequence += 1
            self._rows[feedback.feedback_id] = _Row(
                self._sequence, tenant, key, feedback.model_dump_json()
            )
        return feedback

    async def get(self, feedback_id: UUID) -> FeedbackEvent | None:
        # SELECT event FROM feedback_events WHERE feedback_id = :feedback_id
        with self._lock:
            row = self._rows.get(feedback_id)
        return None if row is None else FeedbackEvent.model_validate_json(row.event)

    async def transition(
        self,
        feedback_id: UUID,
        status: FeedbackStatus,
        *,
        expected: FeedbackStatus,
        resolution: JsonObject | None = None,
    ) -> FeedbackEvent:
        with self._lock:  # one transaction: SELECT ... FOR UPDATE, then UPDATE
            row = self._rows.get(feedback_id)
            if row is None:
                raise FeedbackNotFoundError("unknown feedback event", feedback_id=feedback_id)
            current = FeedbackEvent.model_validate_json(row.event)
            if current.status != expected:
                raise FeedbackConflictError(
                    f"feedback is {current.status}, not the expected {expected}",
                    feedback_id=feedback_id,
                )
            if validate_transition(feedback_id, expected, status).idempotent:
                return current
            updated = current.with_status(status, resolution=resolution)
            # UPDATE feedback_events SET status = :status, event = :event
            #   WHERE feedback_id = :feedback_id AND status = :expected
            row.event = updated.model_dump_json()
        return updated

    async def query(self, query: FeedbackQuery) -> Sequence[FeedbackEvent]:
        # SELECT event FROM feedback_events WHERE <each set filter>
        #   ORDER BY sequence [DESC when newest_first] LIMIT :limit
        # Translate every FeedbackQuery field into SQL; query.matches is the reference.
        with self._lock:
            rows = sorted(self._rows.values(), key=lambda row: row.sequence)
        if query.newest_first:
            rows.reverse()
        matches: list[FeedbackEvent] = []
        for row in rows:
            event = FeedbackEvent.model_validate_json(row.event)
            if query.matches(event):
                matches.append(event)
                if len(matches) == query.limit:
                    break
        return matches
