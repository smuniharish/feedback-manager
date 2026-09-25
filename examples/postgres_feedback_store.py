"""A real ``FeedbackStore`` implementation backed by PostgreSQL.

This demonstrates the intended extension pattern (Section 6 of the spec:
"Custom database/ORM" is explicitly *out of scope* for feedback-manager
itself -- applications bring their own persistence by implementing the
:class:`~feedback_manager.contracts.store.FeedbackStore` ABC). This module
is deliberately shipped under ``examples/``, not ``src/feedback_manager/``,
because production persistence integrations are application concerns, not
package concerns.

Requires ``psycopg[binary]>=3`` (see the ``examples`` dependency group in
``pyproject.toml``) and a reachable PostgreSQL instance. Tested against
PostgreSQL 16 running in a Podman container:

    podman run -d --name fm-postgres \\
        -e POSTGRES_PASSWORD=feedback -e POSTGRES_USER=feedback \\
        -e POSTGRES_DB=feedback_manager -p 5432:5432 \\
        docker.io/library/postgres:16-alpine

Run the smoke test with::

    uv run python examples/postgres_feedback_store.py
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from feedback_manager.contracts.store import FeedbackQuery, FeedbackStore
from feedback_manager.core.events import FeedbackEvent
from feedback_manager.core.lifecycle import validate_transition
from feedback_manager.core.status import FeedbackStatus
from feedback_manager.errors import FeedbackNotFoundError, FeedbackStoreError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback_events (
    feedback_id UUID PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    status TEXT NOT NULL,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS feedback_events_status_idx ON feedback_events (status);
"""


class PostgresFeedbackStore(FeedbackStore):
    """A production-shaped :class:`FeedbackStore` backed by PostgreSQL.

    Events are stored as JSONB (the full ``FeedbackEvent`` is a frozen
    Pydantic model, so ``model_dump_json``/``model_validate_json`` round
    trip it losslessly); ``status`` is duplicated into its own column so it
    can be indexed and filtered on efficiently without a JSON path
    expression. Idempotency is enforced with a ``UNIQUE`` constraint plus
    ``ON CONFLICT ... DO NOTHING`` -- the same guarantee
    :class:`~feedback_manager.storage.memory.InMemoryFeedbackStore` gives,
    but backed by the database rather than an in-process lock.
    """

    def __init__(self, dsn: str, *, connect_timeout: float = 5.0) -> None:
        self._dsn = dsn
        self._connect_timeout = connect_timeout

    def _connect(self, **kwargs: object) -> Any:
        return psycopg.AsyncConnection.connect(
            self._dsn, connect_timeout=self._connect_timeout, **kwargs
        )

    @classmethod
    async def connect(cls, dsn: str, *, connect_timeout: float = 5.0) -> PostgresFeedbackStore:
        """Create the store and ensure its schema exists.

        ``connect_timeout`` bounds how long the initial TCP/handshake
        attempt may take, so a misconfigured or unreachable DSN fails fast
        (typically within a few seconds) instead of hanging indefinitely --
        e.g. in CI or any environment without a reachable PostgreSQL
        instance.
        """
        store = cls(dsn, connect_timeout=connect_timeout)
        async with await store._connect() as conn:
            await conn.execute(_SCHEMA)
        return store

    async def create(self, feedback: FeedbackEvent) -> FeedbackEvent:
        async with await self._connect() as conn:
            if feedback.idempotency_key is not None:
                existing = await conn.execute(
                    "SELECT data FROM feedback_events WHERE idempotency_key = %s",
                    (feedback.idempotency_key,),
                )
                row = await existing.fetchone()
                if row is not None:
                    return FeedbackEvent.model_validate(row[0])
            try:
                await conn.execute(
                    "INSERT INTO feedback_events (feedback_id, idempotency_key, status, data) "
                    "VALUES (%s, %s, %s, %s)",
                    (
                        feedback.feedback_id,
                        feedback.idempotency_key,
                        feedback.status.value,
                        Jsonb(feedback.model_dump(mode="json")),
                    ),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise FeedbackStoreError(
                    "a feedback event with this id already exists",
                    feedback_id=feedback.feedback_id,
                ) from exc
            return feedback

    async def get(self, feedback_id: UUID) -> FeedbackEvent | None:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT data FROM feedback_events WHERE feedback_id = %s", (feedback_id,)
            )
            row = await cur.fetchone()
            return FeedbackEvent.model_validate(row[0]) if row is not None else None

    async def update(self, feedback: FeedbackEvent) -> FeedbackEvent:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE feedback_events SET status = %s, data = %s WHERE feedback_id = %s",
                (
                    feedback.status.value,
                    Jsonb(feedback.model_dump(mode="json")),
                    feedback.feedback_id,
                ),
            )
            if cur.rowcount == 0:
                raise FeedbackNotFoundError(
                    "cannot update a feedback event that was never created",
                    feedback_id=feedback.feedback_id,
                )
            return feedback

    async def transition(self, feedback_id: UUID, status: FeedbackStatus) -> FeedbackEvent:
        current = await self.get(feedback_id)
        if current is None:
            raise FeedbackNotFoundError(
                "cannot transition an unknown feedback event", feedback_id=feedback_id
            )
        validate_transition(feedback_id, current.status, status)
        updated = current.with_status(status)
        return await self.update(updated)

    async def query(self, query: FeedbackQuery) -> Sequence[FeedbackEvent]:
        clauses: list[str] = []
        params: list[object] = []
        if query.status is not None:
            clauses.append("status = %s")
            params.append(query.status.value)
        if query.source is not None:
            clauses.append("data ->> 'source' = %s")
            params.append(query.source)
        if query.category is not None:
            clauses.append("data ->> 'category' = %s")
            params.append(query.category)
        if query.target_type is not None:
            clauses.append("data -> 'target' ->> 'type' = %s")
            params.append(query.target_type)
        if query.target_id is not None:
            clauses.append("data -> 'target' ->> 'id' = %s")
            params.append(query.target_id)
        if query.correlation_id is not None:
            clauses.append("data -> 'correlation' ->> 'correlation_id' = %s")
            params.append(query.correlation_id)

        sql = "SELECT data FROM feedback_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at"
        if query.limit is not None:
            sql += " LIMIT %s"
            params.append(query.limit)

        async with await self._connect(row_factory=dict_row) as conn:
            cur = await conn.execute(sql, params)
            rows = await cur.fetchall()
            return [FeedbackEvent.model_validate(row["data"]) for row in rows]

    async def list(self) -> Sequence[FeedbackEvent]:
        return await self.query(FeedbackQuery())


async def _smoke_test() -> None:
    """Exercise the store against a real PostgreSQL instance end to end."""
    from feedback_manager import (
        FeedbackCategory,
        FeedbackManager,
        FeedbackQuery,
        FeedbackSource,
        FeedbackTarget,
        FeedbackTargetType,
    )

    dsn = os.environ.get(
        "FEEDBACK_MANAGER_POSTGRES_DSN",
        "postgresql://feedback:feedback@localhost:5432/feedback_manager",
    )
    store = await PostgresFeedbackStore.connect(dsn)
    manager = FeedbackManager(store=store)

    event = await manager.submit(
        source=FeedbackSource.HUMAN,
        category=FeedbackCategory.RATING,
        target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id="postgres-demo"),
        payload={"rating": 5, "comment": "Postgres-backed persistence works"},
    )
    print(f"Created: {event.feedback_id} status={event.status}")

    fetched = await manager.get(event.feedback_id)
    assert fetched is not None
    print(f"Fetched back from Postgres: status={fetched.status} payload={fetched.payload}")

    acknowledged = await manager.acknowledge(event.feedback_id)
    print(f"Acknowledged: status={acknowledged.status}")

    handled = await manager.mark_handled(event.feedback_id)
    print(f"Handled: status={handled.status}")

    resolved = await manager.resolve(event.feedback_id, resolution={"reason": "reviewed"})
    print(f"Resolved: status={resolved.status}")

    results = await manager.query(FeedbackQuery(status=FeedbackStatus.RESOLVED))
    print(f"Query for RESOLVED feedback returned {len(results)} row(s)")

    all_rows = await manager.list()
    print(f"Total rows currently in Postgres table: {len(all_rows)}")


if __name__ == "__main__":
    import selectors
    import sys

    if sys.platform == "win32":
        # psycopg's async mode needs a selector-based loop; Windows defaults
        # to ProactorEventLoop, which does not support it.
        asyncio.run(
            _smoke_test(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    else:
        asyncio.run(_smoke_test())
