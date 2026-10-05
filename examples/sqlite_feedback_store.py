"""A durable `FeedbackStore` in a single SQLite file, using only the standard library.

A complete, minimal implementation of the store contract, for applications
that want feedback to survive restarts without running a database server:

- idempotent creation with ``INSERT ... ON CONFLICT (idempotency_key) DO NOTHING``;
- atomic compare-and-set transitions inside a ``BEGIN IMMEDIATE`` transaction;
- queries in creation order, filtered with `FeedbackQuery.matches`, and
  idempotency-key lookups through the unique index.

Blocking SQLite calls run in worker threads, so the event loop never waits on
the disk. For high write volumes or many processes, use a database server, as
in ``examples/postgres_feedback_store.py``.

Run its self-check with:

    uv run python examples/sqlite_feedback_store.py
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING

from feedback_manager import (
    FeedbackConflictError,
    FeedbackEvent,
    FeedbackManager,
    FeedbackNotFoundError,
    FeedbackQuery,
    FeedbackStoreError,
    FeedbackTarget,
    FeedbackTargetType,
    validate_transition,
)
from feedback_manager.contracts import FeedbackStore

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from feedback_manager import FeedbackStatus
    from feedback_manager.core import JsonObject

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    position        INTEGER PRIMARY KEY AUTOINCREMENT,
    feedback_id     TEXT NOT NULL UNIQUE,
    idempotency_key TEXT UNIQUE,
    status          TEXT NOT NULL,
    event           TEXT NOT NULL
)
"""
_OLDEST_FIRST = "SELECT event FROM feedback ORDER BY position"
_NEWEST_FIRST = "SELECT event FROM feedback ORDER BY position DESC"
_BY_KEY = "SELECT event FROM feedback WHERE idempotency_key = ?"


class SQLiteFeedbackStore(FeedbackStore):
    """Stores feedback in one SQLite database file, created if it does not exist."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        # Autocommit mode: each statement commits unless a transaction is open.
        return sqlite3.connect(self._path, isolation_level=None, timeout=30)

    async def create(self, feedback: FeedbackEvent) -> FeedbackEvent:
        """Insert ``feedback``, or return the event already stored with its idempotency key."""
        return await asyncio.to_thread(self._create, feedback)

    async def get(self, feedback_id: UUID) -> FeedbackEvent | None:
        """Return the event with ``feedback_id``, or ``None``."""
        return await asyncio.to_thread(self._get, feedback_id)

    async def transition(
        self,
        feedback_id: UUID,
        status: FeedbackStatus,
        *,
        expected: FeedbackStatus,
        resolution: JsonObject | None = None,
    ) -> FeedbackEvent:
        """Move an event from ``expected`` to ``status`` in one write transaction."""
        return await asyncio.to_thread(self._transition, feedback_id, status, expected, resolution)

    async def query(self, query: FeedbackQuery) -> Sequence[FeedbackEvent]:
        """Return the matching events in creation order."""
        return await asyncio.to_thread(self._query, query)

    def _create(self, feedback: FeedbackEvent) -> FeedbackEvent:
        with closing(self._connect()) as db:
            try:
                inserted = db.execute(
                    "INSERT INTO feedback (feedback_id, idempotency_key, status, event)"
                    " VALUES (?, ?, ?, ?) ON CONFLICT (idempotency_key) DO NOTHING",
                    (
                        str(feedback.feedback_id),
                        feedback.idempotency_key,
                        feedback.status.value,
                        feedback.model_dump_json(),
                    ),
                ).rowcount
            except sqlite3.IntegrityError as error:
                raise FeedbackStoreError(
                    "a feedback event with this ID already exists",
                    feedback_id=feedback.feedback_id,
                ) from error
            if inserted:
                return feedback
            # The idempotency key is taken: return the event stored with it.
            (stored,) = db.execute(_BY_KEY, (feedback.idempotency_key,)).fetchone()
        return FeedbackEvent.model_validate_json(stored)

    def _get(self, feedback_id: UUID) -> FeedbackEvent | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT event FROM feedback WHERE feedback_id = ?", (str(feedback_id),)
            ).fetchone()
        return None if row is None else FeedbackEvent.model_validate_json(row[0])

    def _transition(
        self,
        feedback_id: UUID,
        status: FeedbackStatus,
        expected: FeedbackStatus,
        resolution: JsonObject | None,
    ) -> FeedbackEvent:
        with closing(self._connect()) as db:
            # Take the write lock first, so that reading the current status and
            # writing the new one form one atomic compare-and-set.
            db.execute("BEGIN IMMEDIATE")
            try:
                event = self._apply(db, feedback_id, status, expected, resolution)
            except BaseException:
                db.execute("ROLLBACK")
                raise
            db.execute("COMMIT")
        return event

    @staticmethod
    def _apply(
        db: sqlite3.Connection,
        feedback_id: UUID,
        status: FeedbackStatus,
        expected: FeedbackStatus,
        resolution: JsonObject | None,
    ) -> FeedbackEvent:
        row = db.execute(
            "SELECT event FROM feedback WHERE feedback_id = ?", (str(feedback_id),)
        ).fetchone()
        if row is None:
            raise FeedbackNotFoundError("unknown feedback event", feedback_id=feedback_id)
        current = FeedbackEvent.model_validate_json(row[0])
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
        db.execute(
            "UPDATE feedback SET status = ?, event = ? WHERE feedback_id = ?",
            (status.value, updated.model_dump_json(), str(feedback_id)),
        )
        return updated

    def _query(self, query: FeedbackQuery) -> list[FeedbackEvent]:
        if query.idempotency_key is not None:
            # The unique index finds the only possible match directly.
            statement, parameters = _BY_KEY, (query.idempotency_key,)
        else:
            statement, parameters = (_NEWEST_FIRST if query.newest_first else _OLDEST_FIRST), ()
        matches: list[FeedbackEvent] = []
        with closing(self._connect()) as db:
            for (stored,) in db.execute(statement, parameters):
                event = FeedbackEvent.model_validate_json(stored)
                if query.matches(event):
                    matches.append(event)
                    if len(matches) == query.limit:
                        break
        return matches


async def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "feedback.db"
        manager = FeedbackManager(store=SQLiteFeedbackStore(path))
        target = FeedbackTarget(type=FeedbackTargetType.GENERATION, id="sqlite-self-check")

        feedback = await manager.submit(
            source="human",
            category="rating",
            target=target,
            payload={"rating": 5},
            idempotency_key="rating:sqlite-self-check",
        )
        retried = await manager.submit(
            source="human",
            category="rating",
            target=target,
            payload={"rating": 5},
            idempotency_key="rating:sqlite-self-check",
        )
        print(f"Stored {feedback.feedback_id} ({feedback.status}) in {path.name}")
        print(f"Idempotent retry returned the same event: {retried == feedback}")

        await manager.acknowledge(feedback.feedback_id)
        await manager.mark_handled(feedback.feedback_id)
        await manager.resolve(feedback.feedback_id, resolution={"reviewed": True})

        # A new store instance on the same file sees the same feedback.
        reopened = await SQLiteFeedbackStore(path).get(feedback.feedback_id)
        assert reopened is not None
        print(f"After reopening: {reopened.status}, resolution={reopened.resolution}")
        ratings = await manager.query(FeedbackQuery(category="rating", newest_first=True))
        print(f"Ratings stored: {len(ratings)}")


if __name__ == "__main__":
    asyncio.run(main())
