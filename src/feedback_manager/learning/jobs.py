"""Durable learning jobs: the queue between feedback changes and the learning engine.

A job names one feedback record and the scope it is learned into. Workers lease
jobs, so a crashed worker's jobs become claimable again once the lease expires.
Processing is idempotent (signal and artifact IDs are deterministic), which makes
at-least-once delivery safe.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from feedback_manager.learning.errors import LearningError
from feedback_manager.learning.models import ArtifactScope

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from feedback_manager.core.status import FeedbackStatus

MAX_ERROR_LENGTH = 2000


def _now() -> datetime:
    return datetime.now(UTC)


class LearningJobStatus(StrEnum):
    """Where a job is in its processing lifecycle."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    DEAD_LETTERED = "dead_lettered"


class LearningJobNotFoundError(LearningError, LookupError):
    """The requested learning job does not exist."""


class LearningLeaseLostError(LearningError):
    """Another worker has reclaimed or completed the job."""


class LearningJob(BaseModel):
    """One unit of background learning for a single feedback lifecycle state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: UUID
    feedback_id: UUID
    feedback_status: str
    scope: ArtifactScope
    status: LearningJobStatus = LearningJobStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    retry_attempt: int = Field(default=0, ge=0)
    available_at: AwareDatetime = Field(default_factory=_now)
    lease_expires_at: AwareDatetime | None = None
    last_error: str | None = Field(default=None, max_length=MAX_ERROR_LENGTH)
    created_at: AwareDatetime = Field(default_factory=_now)
    updated_at: AwareDatetime = Field(default_factory=_now)

    @classmethod
    def for_feedback(
        cls, feedback_id: UUID, feedback_status: FeedbackStatus | str, scope: ArtifactScope
    ) -> LearningJob:
        """Create the job for one feedback state; the ID is stable for deduplication."""
        status = str(feedback_status)
        key = json.dumps(
            [scope.model_dump(mode="json"), str(feedback_id), status],
            sort_keys=True,
            separators=(",", ":"),
        )
        return cls(
            job_id=uuid5(NAMESPACE_URL, f"feedback-manager:learning-job:{key}"),
            feedback_id=feedback_id,
            feedback_status=status,
            scope=scope,
        )


def _truncate(error: str) -> str:
    return error[:MAX_ERROR_LENGTH]


class LearningJobStore(ABC):
    """Persistence for learning jobs.

    Implementations must make ``claim`` atomic: a job is leased by at most one
    worker until its lease expires.
    """

    durable: bool = False
    """Whether jobs and change-feed checkpoints survive restart."""

    @abstractmethod
    async def checkpoint(self, source: str) -> str | None:
        """Return the last durably enqueued change position for a source."""

    @abstractmethod
    async def save_checkpoint(self, source: str, cursor: str) -> None:
        """Advance a checkpoint only after all preceding jobs have been persisted."""

    @abstractmethod
    async def enqueue(self, job: LearningJob) -> bool:
        """Store ``job`` unless a job with the same ID exists; return whether it was added."""

    @abstractmethod
    async def claim(
        self, *, limit: int, lease: timedelta, now: datetime | None = None
    ) -> tuple[LearningJob, ...]:
        """Lease up to ``limit`` due pending jobs and expired running jobs, oldest first."""

    @abstractmethod
    async def complete(self, job_id: UUID, *, attempt: int) -> LearningJob:
        """Mark a job as succeeded."""

    @abstractmethod
    async def retry(
        self, job_id: UUID, *, attempt: int, error: str, available_at: datetime
    ) -> LearningJob:
        """Return a failed job to the queue, due at ``available_at``."""

    @abstractmethod
    async def dead_letter(self, job_id: UUID, *, attempt: int, error: str) -> LearningJob:
        """Park a job that must not be retried automatically."""

    @abstractmethod
    async def requeue(self, job_id: UUID) -> LearningJob:
        """Make a dead-lettered job pending again with its attempt count reset."""

    @abstractmethod
    async def get(self, job_id: UUID) -> LearningJob:
        """Return a job or raise `LearningJobNotFoundError`."""

    @abstractmethod
    async def list(
        self, *, status: LearningJobStatus | None = None, limit: int = 100
    ) -> tuple[LearningJob, ...]:
        """Return up to ``limit`` jobs, oldest first, optionally filtered by status."""

    @abstractmethod
    async def counts(self) -> dict[LearningJobStatus, int]:
        """Return the number of jobs in each status."""

    async def close(self) -> None:  # noqa: B027 - optional hook
        """Release resources; the default does nothing."""


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


def _claimable(job: LearningJob, now: datetime) -> bool:
    if job.status is LearningJobStatus.PENDING:
        return job.available_at <= now
    return (
        job.status is LearningJobStatus.RUNNING
        and job.lease_expires_at is not None
        and job.lease_expires_at <= now
    )


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


class InMemoryLearningJobStore(LearningJobStore):
    """A process-local job store for tests and single-process deployments without recovery."""

    def __init__(self) -> None:
        self._jobs: dict[UUID, LearningJob] = {}
        self._lock = asyncio.Lock()
        self._checkpoints: dict[str, str] = {}

    async def checkpoint(self, source: str) -> str | None:
        """Read a process-local change-feed checkpoint."""
        return self._checkpoints.get(source)

    async def save_checkpoint(self, source: str, cursor: str) -> None:
        """Save a process-local change-feed checkpoint."""
        self._checkpoints[source] = cursor

    async def enqueue(self, job: LearningJob) -> bool:
        """Insert a job unless its stable ID already exists."""
        async with self._lock:
            if job.job_id in self._jobs:
                return False
            self._jobs[job.job_id] = job
            return True

    async def claim(
        self, *, limit: int, lease: timedelta, now: datetime | None = None
    ) -> tuple[LearningJob, ...]:
        """Lease due jobs, incrementing the fencing counter."""
        _check_limit(limit)
        _check_claim(lease, now)
        moment = now or _now()
        async with self._lock:
            due = sorted(
                (job for job in self._jobs.values() if _claimable(job, moment)),
                key=lambda job: (job.available_at, job.created_at),
            )[:limit]
            claimed = tuple(_leased(job, moment, lease) for job in due)
            for job in claimed:
                self._jobs[job.job_id] = job
            return claimed

    async def _replace(
        self, job_id: UUID, change: Callable[[LearningJob], LearningJob]
    ) -> LearningJob:
        async with self._lock:
            current = self._jobs.get(job_id)
            if current is None:
                raise LearningJobNotFoundError(f"learning job {job_id} not found")
            updated = change(current)
            self._jobs[job_id] = updated
            return updated

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
        """Return one job or raise if absent."""
        job = self._jobs.get(job_id)
        if job is None:
            raise LearningJobNotFoundError(f"learning job {job_id} not found")
        return job

    async def list(
        self, *, status: LearningJobStatus | None = None, limit: int = 100
    ) -> tuple[LearningJob, ...]:
        """List jobs in creation order."""
        _check_limit(limit)
        jobs = sorted(self._jobs.values(), key=lambda job: job.created_at)
        return tuple(job for job in jobs if status is None or job.status is status)[:limit]

    async def counts(self) -> dict[LearningJobStatus, int]:
        """Count all jobs without truncation."""
        totals = dict.fromkeys(LearningJobStatus, 0)
        for job in self._jobs.values():
            totals[job.status] += 1
        return totals


_SCHEMA = """
CREATE TABLE IF NOT EXISTS learning_jobs (
    job_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    available_at TEXT NOT NULL,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    document TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS learning_jobs_due ON learning_jobs (status, available_at);
CREATE TABLE IF NOT EXISTS learning_checkpoints (
    source TEXT PRIMARY KEY, cursor TEXT NOT NULL
);
"""


def _ts(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class SQLiteLearningJobStore(LearningJobStore):
    """A durable job store in one SQLite file, for single-host deployments.

    Jobs survive restarts; leases make claims safe across processes sharing the
    file. SQLite serializes writers, so use a networked store for many hosts.
    """

    durable = True

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.executescript(_SCHEMA)
        self._connection.commit()

    def _checkpoint(self, source: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT cursor FROM learning_checkpoints WHERE source=?", (source,)
            ).fetchone()
        return None if row is None else row[0]

    async def checkpoint(self, source: str) -> str | None:
        """Read the durable position of a change-feed consumer."""
        return await asyncio.to_thread(self._checkpoint, source)

    def _save_checkpoint(self, source: str, cursor: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO learning_checkpoints VALUES (?,?)", (source, cursor)
            )

    async def save_checkpoint(self, source: str, cursor: str) -> None:
        """Persist a position after the corresponding jobs have been recorded."""
        await asyncio.to_thread(self._save_checkpoint, source, cursor)

    def _write(self, job: LearningJob) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO learning_jobs VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(job.job_id),
                job.status.value,
                _ts(job.available_at),
                _ts(job.lease_expires_at),
                _ts(job.created_at),
                job.model_dump_json(),
            ),
        )

    def _read(self, job_id: UUID) -> LearningJob:
        row = self._connection.execute(
            "SELECT document FROM learning_jobs WHERE job_id = ?", (str(job_id),)
        ).fetchone()
        if row is None:
            raise LearningJobNotFoundError(f"learning job {job_id} not found")
        return LearningJob.model_validate_json(row[0])

    def _enqueue(self, job: LearningJob) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO learning_jobs VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(job.job_id),
                    job.status.value,
                    _ts(job.available_at),
                    _ts(job.lease_expires_at),
                    _ts(job.created_at),
                    job.model_dump_json(),
                ),
            )
            return cursor.rowcount == 1

    async def enqueue(self, job: LearningJob) -> bool:
        """Persist a job unless its stable ID already exists."""
        return await asyncio.to_thread(self._enqueue, job)

    def _claim(self, limit: int, lease: timedelta, now: datetime) -> tuple[LearningJob, ...]:
        moment = _ts(now)
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            rows = self._connection.execute(
                "SELECT document FROM learning_jobs WHERE "
                "(status = 'pending' AND available_at <= ?) OR "
                "(status = 'running' AND lease_expires_at <= ?) "
                "ORDER BY available_at, created_at LIMIT ?",
                (moment, moment, limit),
            ).fetchall()
            claimed = tuple(
                _leased(LearningJob.model_validate_json(row[0]), now, lease) for row in rows
            )
            for job in claimed:
                self._write(job)
            return claimed

    async def claim(
        self, *, limit: int, lease: timedelta, now: datetime | None = None
    ) -> tuple[LearningJob, ...]:
        """Atomically lease due jobs across connections."""
        _check_limit(limit)
        _check_claim(lease, now)
        return await asyncio.to_thread(self._claim, limit, lease, now or _now())

    def _replace_sync(
        self, job_id: UUID, change: Callable[[LearningJob], LearningJob]
    ) -> LearningJob:
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            updated = change(self._read(job_id))
            self._write(updated)
            return updated

    async def _replace(
        self, job_id: UUID, change: Callable[[LearningJob], LearningJob]
    ) -> LearningJob:
        return await asyncio.to_thread(self._replace_sync, job_id, change)

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

    def _get(self, job_id: UUID) -> LearningJob:
        with self._lock:
            return self._read(job_id)

    async def get(self, job_id: UUID) -> LearningJob:
        """Read a durable job by ID."""
        return await asyncio.to_thread(self._get, job_id)

    def _list(self, status: LearningJobStatus | None, limit: int) -> Sequence[LearningJob]:
        with self._lock:
            if status is None:
                rows = self._connection.execute(
                    "SELECT document FROM learning_jobs ORDER BY created_at LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT document FROM learning_jobs WHERE status = ? "
                    "ORDER BY created_at LIMIT ?",
                    (status.value, limit),
                ).fetchall()
        return [LearningJob.model_validate_json(row[0]) for row in rows]

    async def list(
        self, *, status: LearningJobStatus | None = None, limit: int = 100
    ) -> tuple[LearningJob, ...]:
        """List durable jobs in creation order."""
        _check_limit(limit)
        return tuple(await asyncio.to_thread(self._list, status, limit))

    def _counts(self) -> dict[LearningJobStatus, int]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT status, COUNT(*) FROM learning_jobs GROUP BY status"
            ).fetchall()
        totals = dict.fromkeys(LearningJobStatus, 0)
        for status, count in rows:
            totals[LearningJobStatus(status)] = count
        return totals

    async def counts(self) -> dict[LearningJobStatus, int]:
        """Count all durable jobs by status."""
        return await asyncio.to_thread(self._counts)

    async def close(self) -> None:
        """Close the SQLite connection."""
        with self._lock:
            self._connection.close()


__all__ = [
    "InMemoryLearningJobStore",
    "LearningJob",
    "LearningJobNotFoundError",
    "LearningJobStatus",
    "LearningJobStore",
    "LearningLeaseLostError",
    "SQLiteLearningJobStore",
]
