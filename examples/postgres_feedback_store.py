"""A production-ready `FeedbackStore` backed by PostgreSQL.

Copy it into your application and adapt the schema to your conventions. It
implements the store contract with database guarantees instead of an
in-process lock:

- one connection pool shared by every operation (``psycopg_pool``);
- idempotent creation with ``INSERT ... ON CONFLICT (idempotency_key) DO NOTHING``;
- compare-and-set transitions in a single ``UPDATE ... WHERE status = <expected>``;
- typed, indexed columns for every query filter, next to the full event as JSONB.

Requires the ``examples`` dependency group and a reachable PostgreSQL:

    uv sync --group examples
    docker compose -f examples/compose.yaml up -d postgres

Then run its self-check against that database:

    FEEDBACK_MANAGER_POSTGRES_DSN=postgresql://feedback:feedback@localhost:5432/feedback \\
        uv run python examples/postgres_feedback_store.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import TYPE_CHECKING, Any, Self

from psycopg import errors, sql
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from feedback_manager import (
    FeedbackConflictError,
    FeedbackEvent,
    FeedbackManager,
    FeedbackNotFoundError,
    FeedbackQuery,
    FeedbackStatus,
    FeedbackStoreError,
    FeedbackTarget,
    FeedbackTargetType,
    validate_transition,
)
from feedback_manager.contracts import FeedbackStore

if TYPE_CHECKING:
    from collections.abc import Coroutine, Sequence
    from uuid import UUID

    from feedback_manager.core import JsonObject

DSN_VARIABLE = "FEEDBACK_MANAGER_POSTGRES_DSN"
"""The environment variable the examples read the PostgreSQL connection string from."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS {table} (
    position        BIGINT GENERATED ALWAYS AS IDENTITY,
    feedback_id     UUID PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    source          TEXT NOT NULL,
    category        TEXT NOT NULL,
    target_type     TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    status          TEXT NOT NULL,
    correlation_id  TEXT,
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,
    event           JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS {position_index} ON {table} (position);
CREATE INDEX IF NOT EXISTS {status_index} ON {table} (status, position);
CREATE INDEX IF NOT EXISTS {correlation_index} ON {table} (correlation_id, position);
CREATE INDEX IF NOT EXISTS {target_index} ON {table} (target_type, target_id);
CREATE INDEX IF NOT EXISTS {created_index} ON {table} (created_at);
"""
_FILTER_COLUMNS = (
    "source",
    "category",
    "target_type",
    "target_id",
    "status",
    "correlation_id",
    "idempotency_key",
)


class PostgresFeedbackStore(FeedbackStore):
    """Stores feedback in one PostgreSQL table.

    Create it with `open`, which opens the connection pool and creates the
    table and indexes if needed, and close it with `close` or ``async with``.
    """

    def __init__(self, pool: AsyncConnectionPool, *, table: str = "feedback_events") -> None:
        self._pool = pool
        self._table_name = table
        self._table = sql.Identifier(table)

    @classmethod
    async def open(
        cls,
        dsn: str,
        *,
        table: str = "feedback_events",
        max_connections: int = 10,
        connect_timeout: float = 10.0,
    ) -> Self:
        """Open a connection pool to ``dsn`` and ensure the schema exists.

        Fails within ``connect_timeout`` seconds when the database is unreachable.
        """
        pool = AsyncConnectionPool(
            dsn,
            min_size=1,
            max_size=max_connections,
            timeout=connect_timeout,
            open=False,
            kwargs={"autocommit": True},
        )
        await pool.open(wait=True, timeout=connect_timeout)
        store = cls(pool, table=table)
        try:
            await store._create_schema()
        except BaseException:
            await pool.close()
            raise
        return store

    async def close(self) -> None:
        """Close the connection pool."""
        await self._pool.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def create(self, feedback: FeedbackEvent) -> FeedbackEvent:
        """Insert ``feedback``, or return the event already stored with its idempotency key."""
        insert = sql.SQL(
            "INSERT INTO {table} (feedback_id, idempotency_key, source, category, target_type,"
            " target_id, status, correlation_id, created_at, updated_at, event)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (idempotency_key) DO NOTHING RETURNING feedback_id"
        ).format(table=self._table)
        try:
            inserted = await self._fetch(
                insert,
                (
                    feedback.feedback_id,
                    feedback.idempotency_key,
                    str(feedback.source),
                    str(feedback.category),
                    str(feedback.target.type),
                    feedback.target.id,
                    feedback.status.value,
                    feedback.correlation_id,
                    feedback.created_at,
                    feedback.updated_at,
                    Jsonb(feedback.model_dump(mode="json")),
                ),
            )
        except errors.UniqueViolation as exc:
            raise FeedbackStoreError(
                "a feedback event with this ID already exists", feedback_id=feedback.feedback_id
            ) from exc
        if inserted:
            return feedback
        # Another submission with the same idempotency key won; return its event.
        (existing,) = await self._fetch(
            sql.SQL("SELECT event FROM {table} WHERE idempotency_key = %s").format(
                table=self._table
            ),
            (feedback.idempotency_key,),
        )
        return FeedbackEvent.model_validate(existing[0])

    async def get(self, feedback_id: UUID) -> FeedbackEvent | None:
        """Return the event with ``feedback_id``, or ``None``."""
        rows = await self._fetch(
            sql.SQL("SELECT event FROM {table} WHERE feedback_id = %s").format(table=self._table),
            (feedback_id,),
        )
        return FeedbackEvent.model_validate(rows[0][0]) if rows else None

    async def transition(
        self,
        feedback_id: UUID,
        status: FeedbackStatus,
        *,
        expected: FeedbackStatus,
        resolution: JsonObject | None = None,
    ) -> FeedbackEvent:
        """Move an event from ``expected`` to ``status`` with a compare-and-set ``UPDATE``."""
        current = await self._require(feedback_id, expected)
        if validate_transition(feedback_id, expected, status).idempotent:
            return current
        updated = current.with_status(status, resolution=resolution)
        rows = await self._fetch(
            sql.SQL(
                "UPDATE {table} SET status = %s, updated_at = %s, event = %s"
                " WHERE feedback_id = %s AND status = %s RETURNING feedback_id"
            ).format(table=self._table),
            (
                status.value,
                updated.updated_at,
                Jsonb(updated.model_dump(mode="json")),
                feedback_id,
                expected.value,
            ),
        )
        if not rows:
            # Another writer moved the event on between the read and the update.
            raise FeedbackConflictError(
                "feedback changed concurrently",
                feedback_id=feedback_id,
                requested_status=status.value,
            )
        return updated

    async def query(self, query: FeedbackQuery) -> Sequence[FeedbackEvent]:
        """Return the matching events, filtered and ordered by the database."""
        clauses: list[sql.Composable] = []
        params: list[Any] = []
        for column in _FILTER_COLUMNS:
            value = getattr(query, column)
            if value is not None:
                clauses.append(sql.SQL("{} = %s").format(sql.Identifier(column)))
                params.append(str(value))
        if query.created_after is not None:
            clauses.append(sql.SQL("created_at >= %s"))
            params.append(query.created_after)
        if query.created_before is not None:
            clauses.append(sql.SQL("created_at < %s"))
            params.append(query.created_before)
        statement = sql.SQL("SELECT event FROM {table}").format(table=self._table)
        if clauses:
            statement += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(clauses)
        statement += sql.SQL(
            " ORDER BY position DESC" if query.newest_first else " ORDER BY position"
        )
        if query.limit is not None:
            statement += sql.SQL(" LIMIT %s")
            params.append(query.limit)
        rows = await self._fetch(statement, params)
        return [FeedbackEvent.model_validate(row[0]) for row in rows]

    async def _require(self, feedback_id: UUID, expected: FeedbackStatus) -> FeedbackEvent:
        current = await self.get(feedback_id)
        if current is None:
            raise FeedbackNotFoundError("unknown feedback event", feedback_id=feedback_id)
        if current.status != expected:
            raise FeedbackConflictError(
                f"feedback is {current.status}, not the expected {expected}",
                feedback_id=feedback_id,
                current_status=current.status.value,
            )
        return current

    async def _create_schema(self) -> None:
        name = self._table_name
        schema = sql.SQL(_SCHEMA).format(
            table=self._table,
            position_index=sql.Identifier(f"{name}_position_idx"),
            status_index=sql.Identifier(f"{name}_status_idx"),
            correlation_index=sql.Identifier(f"{name}_correlation_idx"),
            target_index=sql.Identifier(f"{name}_target_idx"),
            created_index=sql.Identifier(f"{name}_created_idx"),
        )
        async with self._pool.connection() as connection:
            await connection.execute(schema)

    async def _fetch(self, statement: sql.Composed, params: Sequence[Any]) -> list[tuple[Any, ...]]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(statement, params)
            return await cursor.fetchall()


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """Run ``coroutine`` like `asyncio.run`, on an event loop psycopg supports.

    psycopg's async mode needs a selector event loop, which is not the default
    on Windows.
    """
    if sys.platform == "win32":
        return asyncio.run(coroutine, loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(coroutine)


async def main() -> None:
    dsn = os.environ.get(DSN_VARIABLE)
    if not dsn:
        sys.exit(f"Set {DSN_VARIABLE} to a PostgreSQL connection string first.")
    async with await PostgresFeedbackStore.open(dsn) as store:
        manager = FeedbackManager(store=store)
        target = FeedbackTarget(type=FeedbackTargetType.GENERATION, id="postgres-self-check")

        feedback = await manager.submit(
            source="human", category="rating", target=target, payload={"rating": 5}
        )
        retried = await manager.submit(
            source="human",
            category="rating",
            target=target,
            payload={"rating": 5},
            idempotency_key=f"self-check-{feedback.feedback_id}",
        )
        again = await manager.submit(
            source="human",
            category="rating",
            target=target,
            idempotency_key=f"self-check-{feedback.feedback_id}",
        )
        print(f"Stored {feedback.feedback_id} ({feedback.status})")
        print(
            f"Idempotent retry returned the same event: {again.feedback_id == retried.feedback_id}"
        )

        await manager.acknowledge(feedback.feedback_id)
        await manager.mark_handled(feedback.feedback_id)
        resolved = await manager.resolve(feedback.feedback_id, resolution={"reviewed": True})
        print(f"Resolved: {resolved.status}, resolution={resolved.resolution}")

        recent = await manager.query(FeedbackQuery(target_id=target.id, newest_first=True, limit=3))
        print(f"Latest self-check events: {[str(event.status) for event in recent]}")


if __name__ == "__main__":
    run(main())
