"""A networked `LearningJobStore` backed by PostgreSQL, for workers on many hosts.

The bundled `SQLiteLearningJobStore` serializes writers through one file and
only works on a single host. This store lets every host point at the same
database. Claims run inside one transaction with ``SELECT ... FOR UPDATE SKIP
LOCKED``, so two hosts never lease the same job, and every state change is a
compare-and-set on the attempt number (fencing) as the core contract requires.

Copy it into your application and adapt the schema to your conventions. Verify
any change with `LearningJobStoreContract` (see ``tests/examples/test_learning_jobs.py``).

Requires the ``examples`` dependency group and a reachable PostgreSQL:

    uv sync --group examples
    docker compose -f examples/compose.yaml up -d postgres

Then run its self-check against that database:

    FEEDBACK_MANAGER_POSTGRES_DSN=postgresql://feedback:feedback@localhost:5432/feedback \\
        uv run python examples/postgres_learning_job_store.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Self
from uuid import uuid4

from psycopg import sql
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from feedback_manager.learning import ArtifactScope, LearningError
from feedback_manager.learning.jobs import (
    LearningJob,
    LearningJobNotFoundError,
    LearningJobStatus,
    LearningJobStore,
    LearningLeaseLostError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from uuid import UUID

DSN_VARIABLE = "FEEDBACK_MANAGER_POSTGRES_DSN"
"""The environment variable the examples read the PostgreSQL connection string from."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS {table} (
    job_id           UUID PRIMARY KEY,
    status           TEXT NOT NULL,
    available_at     TIMESTAMPTZ NOT NULL,
    lease_expires_at TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL,
    document         JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS {due_index} ON {table} (status, available_at);
CREATE INDEX IF NOT EXISTS {lease_index} ON {table} (status, lease_expires_at);
CREATE TABLE IF NOT EXISTS {checkpoints} (
    source TEXT PRIMARY KEY,
    cursor TEXT NOT NULL
);
"""

# The transition rules below mirror the in-memory store in feedback_manager.learning.jobs.
# That module keeps them private, so this example carries its own copy; keep them in step.
MAX_ERROR_LENGTH = 2000


def _now() -> datetime:
    return datetime.now(UTC)


def _truncate(error: str) -> str:
    return error[:MAX_ERROR_LENGTH]


def _check_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")


def _check_claim(lease: timedelta, now: datetime | None) -> None:
    if lease <= timedelta(0):
        raise ValueError("lease must be positive")
    if now is not None and now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")


def _check_lease(job: LearningJob, attempt: int) -> None:
    if job.status is not LearningJobStatus.RUNNING or job.attempts != attempt:
        raise LearningLeaseLostError("learning job lease was lost")


def _leased(job: LearningJob, now: datetime, lease: timedelta) -> LearningJob:
    return job.model_copy(
        update={
            "status": LearningJobStatus.RUNNING,
            "attempts": job.attempts + 1,
            "retry_attempt": job.retry_attempt + 1,
            "lease_expires_at": now + lease,
            "updated_at": now,
        }
    )


def _completed(job: LearningJob, attempt: int) -> LearningJob:
    _check_lease(job, attempt)
    return job.model_copy(
        update={
            "status": LearningJobStatus.SUCCEEDED,
            "lease_expires_at": None,
            "last_error": None,
            "updated_at": _now(),
        }
    )


def _retried(job: LearningJob, attempt: int, error: str, available_at: datetime) -> LearningJob:
    _check_lease(job, attempt)
    if available_at.utcoffset() is None:
        raise ValueError("available_at must be timezone-aware")
    return job.model_copy(
        update={
            "status": LearningJobStatus.PENDING,
            "available_at": available_at,
            "lease_expires_at": None,
            "last_error": _truncate(error),
            "updated_at": _now(),
        }
    )


def _dead(job: LearningJob, attempt: int, error: str) -> LearningJob:
    _check_lease(job, attempt)
    return job.model_copy(
        update={
            "status": LearningJobStatus.DEAD_LETTERED,
            "lease_expires_at": None,
            "last_error": _truncate(error),
            "updated_at": _now(),
        }
    )


def _requeued(job: LearningJob) -> LearningJob:
    if job.status is not LearningJobStatus.DEAD_LETTERED:
        raise LearningError("only dead-lettered jobs can be requeued")
    now = _now()
    return job.model_copy(
        update={
            "status": LearningJobStatus.PENDING,
            "retry_attempt": 0,
            "available_at": now,
            "updated_at": now,
        }
    )


class PostgresLearningJobStore(LearningJobStore):
    """Durable learning jobs shared by every worker that uses the same database.

    Create it with `open`, which opens the connection pool and creates the
    tables if needed, and close it with `close` or ``async with``.
    """

    durable = True

    def __init__(self, pool: AsyncConnectionPool, *, table: str = "learning_jobs") -> None:
        self._pool = pool
        self._table = sql.Identifier(table)
        self._due_index = sql.Identifier(f"{table}_due")
        self._lease_index = sql.Identifier(f"{table}_lease")
        self._checkpoints = sql.Identifier(f"{table}_checkpoints")

    @classmethod
    async def open(
        cls,
        dsn: str,
        *,
        table: str = "learning_jobs",
        max_connections: int = 10,
        connect_timeout: float = 10.0,
    ) -> Self:
        """Open a connection pool to ``dsn`` and ensure the schema exists."""
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

    async def _create_schema(self) -> None:
        statements = sql.SQL(_SCHEMA).format(
            table=self._table,
            due_index=self._due_index,
            lease_index=self._lease_index,
            checkpoints=self._checkpoints,
        )
        async with self._pool.connection() as connection:
            await connection.execute(statements)

    async def drop_schema(self) -> None:
        """Drop the job and checkpoint tables; intended for tests and demos only."""
        async with self._pool.connection() as connection:
            await connection.execute(sql.SQL("DROP TABLE {}").format(self._table))
            await connection.execute(sql.SQL("DROP TABLE {}").format(self._checkpoints))

    async def _transaction(self, work: Callable[[Any], Any]) -> Any:
        """Run ``work(connection)`` in one transaction; any exception rolls it back."""
        async with self._pool.connection() as connection, connection.transaction():
            return await work(connection)

    async def checkpoint(self, source: str) -> str | None:
        """Read the durable position of a change-feed consumer."""
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                sql.SQL("SELECT cursor FROM {} WHERE source = %s").format(self._checkpoints),
                (source,),
            )
            row = await cursor.fetchone()
        return None if row is None else row[0]

    async def save_checkpoint(self, source: str, cursor: str) -> None:
        """Persist a position after the corresponding jobs have been recorded."""
        async with self._pool.connection() as connection:
            await connection.execute(
                sql.SQL(
                    "INSERT INTO {} (source, cursor) VALUES (%s, %s)"
                    " ON CONFLICT (source) DO UPDATE SET cursor = EXCLUDED.cursor"
                ).format(self._checkpoints),
                (source, cursor),
            )

    async def enqueue(self, job: LearningJob) -> bool:
        """Persist a job unless its stable ID already exists."""
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                sql.SQL(
                    "INSERT INTO {} (job_id, status, available_at, lease_expires_at, created_at,"
                    " document) VALUES (%s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (job_id) DO NOTHING"
                ).format(self._table),
                (
                    job.job_id,
                    job.status.value,
                    job.available_at,
                    job.lease_expires_at,
                    job.created_at,
                    Jsonb(job.model_dump(mode="json")),
                ),
            )
            return cursor.rowcount == 1

    async def claim(
        self, *, limit: int, lease: timedelta, now: datetime | None = None
    ) -> tuple[LearningJob, ...]:
        """Lease due jobs; rows locked by another host are skipped, not waited on."""
        _check_limit(limit)
        _check_claim(lease, now)
        moment = now or datetime.now(UTC)

        async def work(connection: Any) -> tuple[LearningJob, ...]:
            cursor = await connection.execute(
                sql.SQL(
                    "SELECT document FROM {} WHERE"
                    " (status = 'pending' AND available_at <= %s)"
                    " OR (status = 'running' AND lease_expires_at <= %s)"
                    " ORDER BY available_at, created_at LIMIT %s FOR UPDATE SKIP LOCKED"
                ).format(self._table),
                (moment, moment, limit),
            )
            rows = await cursor.fetchall()
            claimed = tuple(
                _leased(LearningJob.model_validate(row[0]), moment, lease) for row in rows
            )
            for job in claimed:
                await self._write(connection, job)
            return claimed

        return await self._transaction(work)

    async def _write(self, connection: Any, job: LearningJob) -> None:
        await connection.execute(
            sql.SQL(
                "UPDATE {} SET status = %s, available_at = %s, lease_expires_at = %s,"
                " document = %s WHERE job_id = %s"
            ).format(self._table),
            (
                job.status.value,
                job.available_at,
                job.lease_expires_at,
                Jsonb(job.model_dump(mode="json")),
                job.job_id,
            ),
        )

    async def _replace(
        self, job_id: UUID, change: Callable[[LearningJob], LearningJob]
    ) -> LearningJob:
        async def work(connection: Any) -> LearningJob:
            cursor = await connection.execute(
                sql.SQL("SELECT document FROM {} WHERE job_id = %s FOR UPDATE").format(self._table),
                (job_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise LearningJobNotFoundError(f"learning job {job_id} not found")
            updated = change(LearningJob.model_validate(row[0]))
            await self._write(connection, updated)
            return updated

        return await self._transaction(work)

    async def complete(self, job_id: UUID, *, attempt: int) -> LearningJob:
        """Complete the current claim, rejecting stale workers."""
        return await self._replace(job_id, lambda job: _completed(job, attempt))

    async def retry(
        self, job_id: UUID, *, attempt: int, error: str, available_at: datetime
    ) -> LearningJob:
        """Schedule the current claim for retry."""
        return await self._replace(job_id, lambda job: _retried(job, attempt, error, available_at))

    async def dead_letter(self, job_id: UUID, *, attempt: int, error: str) -> LearningJob:
        """Park the current claim for operator intervention."""
        return await self._replace(job_id, lambda job: _dead(job, attempt, error))

    async def requeue(self, job_id: UUID) -> LearningJob:
        """Requeue a dead letter while preserving its fencing counter."""
        return await self._replace(job_id, _requeued)

    async def get(self, job_id: UUID) -> LearningJob:
        """Read a durable job by ID."""
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                sql.SQL("SELECT document FROM {} WHERE job_id = %s").format(self._table),
                (job_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise LearningJobNotFoundError(f"learning job {job_id} not found")
        return LearningJob.model_validate(row[0])

    async def list(
        self, *, status: LearningJobStatus | None = None, limit: int = 100
    ) -> tuple[LearningJob, ...]:
        """List jobs in creation order, optionally filtered by status."""
        _check_limit(limit)
        clause = sql.SQL("") if status is None else sql.SQL("WHERE status = %s")
        params: tuple[object, ...] = (limit,) if status is None else (status.value, limit)
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                sql.SQL("SELECT document FROM {} {} ORDER BY created_at LIMIT %s").format(
                    self._table, clause
                ),
                params,
            )
            rows: Sequence[tuple[Any, ...]] = await cursor.fetchall()
        return tuple(LearningJob.model_validate(row[0]) for row in rows)

    async def counts(self) -> dict[LearningJobStatus, int]:
        """Count all jobs in each status without truncation."""
        totals = dict.fromkeys(LearningJobStatus, 0)
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                sql.SQL("SELECT status, count(*) FROM {} GROUP BY status").format(self._table)
            )
            for status, count in await cursor.fetchall():
                totals[LearningJobStatus(status)] = count
        return totals


async def run(dsn: str) -> None:
    """Self-check: enqueue, claim from two store instances, and complete once each."""
    scope = ArtifactScope(tenant_id="example-tenant", namespace="default", agent_id="researcher")
    table = f"learning_jobs_demo_{uuid4().hex[:8]}"
    first = await PostgresLearningJobStore.open(dsn, table=table)
    second = await PostgresLearningJobStore.open(dsn, table=table)
    try:
        for _ in range(4):
            await first.enqueue(LearningJob.for_feedback(uuid4(), "received", scope))
        claims = await asyncio.gather(
            first.claim(limit=2, lease=timedelta(minutes=5)),
            second.claim(limit=4, lease=timedelta(minutes=5)),
        )
        claimed = [job for batch in claims for job in batch]
        if len({job.job_id for job in claimed}) != len(claimed) or len(claimed) != 4:
            raise SystemExit(f"duplicate or missing claims: {claims!r}")
        for job in claimed:
            await first.complete(job.job_id, attempt=job.attempts)
        counts = await second.counts()
        print(f"claimed {len(claimed)} jobs across two store instances; counts: {counts}")
    finally:
        await first.drop_schema()
        await first.close()
        await second.close()


def main() -> None:
    dsn = os.environ.get(DSN_VARIABLE)
    if not dsn:
        print(f"set {DSN_VARIABLE} to a PostgreSQL connection string", file=sys.stderr)
        raise SystemExit(2)
    # psycopg's async mode needs a selector event loop, which is not the Windows default.
    asyncio.run(run(dsn), loop_factory=asyncio.SelectorEventLoop)


if __name__ == "__main__":
    main()
