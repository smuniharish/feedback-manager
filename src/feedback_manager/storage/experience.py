"""Authoritative, scoped storage for execution experience versions."""

from __future__ import annotations

import asyncio
import threading
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from feedback_manager.experience import ExecutionExperience
from feedback_manager.learning.errors import LearningError, LearningValidationError
from feedback_manager.storage._sqlite import SQLiteHandle, open_with_cleanup
from feedback_manager.storage.payload import PayloadScope, _check_scope

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path


class ExperienceStoreError(LearningError):
    """Base class for experience archive failures."""


class ExperienceNotFoundError(ExperienceStoreError, LookupError):
    """An experience is not present in the requested scope."""


class ExperienceConflictError(ExperienceStoreError):
    """An experience ID or expected version conflicts with the archive."""


class ExperiencePage(BaseModel):
    """A bounded page of current experience versions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    experiences: tuple[ExecutionExperience, ...]
    next_cursor: UUID | None = None


class ExperienceRepositoryABC(ABC):
    """Authoritative experience snapshots, isolated by an authenticated scope.

    A host authorizes scope before accessing this repository. `delete` removes
    only experience versions; use `ExperiencePurger` to also remove assessments,
    payloads and learned influence. Payload references are not checked here.
    """

    durable: bool = False

    @abstractmethod
    async def create(self, experience: ExecutionExperience) -> ExecutionExperience:
        """Create version one, or return the current version for an identical retry."""

    @abstractmethod
    async def update(
        self, experience: ExecutionExperience, *, expected_version: int
    ) -> ExecutionExperience:
        """Append a version using optimistic concurrency control."""

    @abstractmethod
    async def get(
        self, scope: PayloadScope, experience_id: UUID, *, version: int | None = None
    ) -> ExecutionExperience:
        """Return a version or raise `ExperienceNotFoundError`."""

    @abstractmethod
    async def versions(
        self,
        scope: PayloadScope,
        experience_id: UUID,
        *,
        after_version: int = 0,
        limit: int = 50,
    ) -> Sequence[ExecutionExperience]:
        """Return up to `limit` versions in ascending order after the given version."""

    @abstractmethod
    async def query(
        self, scope: PayloadScope, *, limit: int = 50, cursor: UUID | None = None
    ) -> ExperiencePage:
        """Return current versions in insertion order; pages are not a snapshot."""

    @abstractmethod
    async def delete(self, scope: PayloadScope, experience_id: UUID) -> bool:
        """Delete every version of a scoped experience; return whether it existed."""

    @abstractmethod
    async def health(self) -> None:
        """Check repository availability."""


class InMemoryExperienceRepository(ExperienceRepositoryABC):
    """Keep process-local experience histories; no durable guarantees are made.

    Operations run in worker threads; cancellation does not roll back an
    operation that has already started.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[PayloadScope, dict[UUID, list[ExecutionExperience]]] = {}

    async def create(self, experience: ExecutionExperience) -> ExecutionExperience:
        """Create an experience or return the latest version for an identical retry."""
        return await asyncio.to_thread(self._create, experience)

    def _create(self, experience: ExecutionExperience) -> ExecutionExperience:
        snapshot = _copy(experience)
        if snapshot.version != 1:
            raise ExperienceConflictError("new experiences must be version 1")
        with self._lock:
            records = self._records.setdefault(snapshot.scope, {})
            history = records.get(snapshot.experience_id)
            if history is not None:
                if history[0] == snapshot:
                    return _copy(history[-1])
                raise ExperienceConflictError("experience ID already exists")
            records[snapshot.experience_id] = [snapshot]
            return _copy(snapshot)

    async def update(
        self, experience: ExecutionExperience, *, expected_version: int
    ) -> ExecutionExperience:
        """Append a version if its predecessor is still current."""
        return await asyncio.to_thread(self._update, experience, expected_version)

    def _update(
        self, experience: ExecutionExperience, expected_version: int
    ) -> ExecutionExperience:
        _positive_integer(expected_version, "expected_version")
        snapshot = _copy(experience)
        with self._lock:
            history = self._records.get(snapshot.scope, {}).get(snapshot.experience_id)
            if history is None:
                raise _not_found()
            _validate_update(history[-1], snapshot, expected_version)
            history.append(snapshot)
            return _copy(snapshot)

    async def get(
        self, scope: PayloadScope, experience_id: UUID, *, version: int | None = None
    ) -> ExecutionExperience:
        """Return a scoped version."""
        return await asyncio.to_thread(self._get, scope, experience_id, version)

    def _get(
        self, scope: PayloadScope, experience_id: UUID, version: int | None
    ) -> ExecutionExperience:
        scope = _key(scope, experience_id)
        if version is not None:
            _positive_integer(version, "version")
        with self._lock:
            history = self._records.get(scope, {}).get(experience_id)
            if history is None or (version is not None and version > len(history)):
                raise _not_found()
            return _copy(history[-1] if version is None else history[version - 1])

    async def versions(
        self,
        scope: PayloadScope,
        experience_id: UUID,
        *,
        after_version: int = 0,
        limit: int = 50,
    ) -> Sequence[ExecutionExperience]:
        """Return a bounded page of scoped versions in ascending order."""
        return await asyncio.to_thread(self._versions, scope, experience_id, after_version, limit)

    def _versions(
        self, scope: PayloadScope, experience_id: UUID, after_version: int, limit: int
    ) -> Sequence[ExecutionExperience]:
        scope = _key(scope, experience_id)
        _check_after_version(after_version)
        _page_limit(limit)
        with self._lock:
            history = self._records.get(scope, {}).get(experience_id)
            if history is None:
                raise _not_found()
            return tuple(_copy(item) for item in history[after_version : after_version + limit])

    async def query(
        self, scope: PayloadScope, *, limit: int = 50, cursor: UUID | None = None
    ) -> ExperiencePage:
        """Return a page of current versions in insertion order."""
        return await asyncio.to_thread(self._query, scope, limit, cursor)

    def _query(self, scope: PayloadScope, limit: int, cursor: UUID | None) -> ExperiencePage:
        scope = _check_scope(scope)
        _page_limit(limit)
        if cursor is not None and not isinstance(cursor, UUID):
            raise LearningValidationError("experience cursor must be a UUID")
        with self._lock:
            records = self._records.get(scope, {})
            after_cursor = cursor is None
            matches: list[ExecutionExperience] = []
            for experience_id, history in records.items():
                if not after_cursor:
                    if experience_id == cursor:
                        after_cursor = True
                    continue
                matches.append(history[-1])
                if len(matches) > limit:
                    break
            if not after_cursor:
                raise LearningValidationError("experience cursor is not in the requested scope")
            page = tuple(_copy(item) for item in matches[:limit])
            return ExperiencePage(
                experiences=page,
                next_cursor=page[-1].experience_id if len(matches) > limit else None,
            )

    async def delete(self, scope: PayloadScope, experience_id: UUID) -> bool:
        """Delete every scoped version of the experience."""
        return await asyncio.to_thread(self._delete, scope, experience_id)

    def _delete(self, scope: PayloadScope, experience_id: UUID) -> bool:
        scope = _key(scope, experience_id)
        with self._lock:
            return self._records.get(scope, {}).pop(experience_id, None) is not None

    async def health(self) -> None:
        """The in-memory repository has no external dependency."""


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS experience_records (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    experience_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    document TEXT NOT NULL,
    UNIQUE (scope, experience_id)
);
CREATE INDEX IF NOT EXISTS experience_records_scope ON experience_records (scope, seq);
CREATE TABLE IF NOT EXISTS experience_versions (
    scope TEXT NOT NULL,
    experience_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    document TEXT NOT NULL,
    PRIMARY KEY (scope, experience_id, version)
);
"""


class SQLiteExperienceRepository(ExperienceRepositoryABC):
    """Durable single-host archive with transactional version appends.

    Use `open` and `close` to avoid blocking the event loop. SQLite serializes
    writers across connections. Cancellation does not roll back an operation
    that has already started in a worker thread.
    """

    durable = True

    def __init__(self, path: str | Path) -> None:
        self._handle = SQLiteHandle(
            path, _SQLITE_SCHEMA, name="experience", error_type=ExperienceStoreError
        )
        self._connection = self._handle.connection

    @classmethod
    async def open(cls, path: str | Path) -> SQLiteExperienceRepository:
        """Open a local archive without blocking the event loop."""
        return await open_with_cleanup(lambda: cls(path), lambda repository: repository.close())

    async def _run[R](self, operation: Callable[[], R]) -> R:
        return await self._handle.run(operation)

    async def create(self, experience: ExecutionExperience) -> ExecutionExperience:
        """Atomically create version one or return the current version for a retry."""

        def create() -> ExecutionExperience:
            snapshot = _copy(experience)
            if snapshot.version != 1:
                raise ExperienceConflictError("new experiences must be version 1")
            scope, identifier = _sqlite_key(snapshot.scope, snapshot.experience_id)
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT version,document FROM experience_versions "
                "WHERE scope=? AND experience_id=? AND version=1",
                (scope, identifier),
            ).fetchone()
            if row is not None:
                if _sqlite_document(scope, identifier, row[0], row[1]) != snapshot:
                    raise ExperienceConflictError("experience ID already exists")
                return self._get_version(scope, identifier, None)
            document = snapshot.model_dump_json()
            self._connection.execute(
                "INSERT INTO experience_records (scope,experience_id,version,document) "
                "VALUES (?,?,?,?)",
                (scope, identifier, 1, document),
            )
            self._connection.execute(
                "INSERT INTO experience_versions VALUES (?,?,?,?)",
                (scope, identifier, 1, document),
            )
            return snapshot

        return await self._run(create)

    async def update(
        self, experience: ExecutionExperience, *, expected_version: int
    ) -> ExecutionExperience:
        """Atomically append the next version if the expected version is current."""

        def update() -> ExecutionExperience:
            _positive_integer(expected_version, "expected_version")
            snapshot = _copy(experience)
            scope, identifier = _sqlite_key(snapshot.scope, snapshot.experience_id)
            self._connection.execute("BEGIN IMMEDIATE")
            current = self._get_version(scope, identifier, None)
            _validate_update(current, snapshot, expected_version)
            document = snapshot.model_dump_json()
            self._connection.execute(
                "UPDATE experience_records SET version=?,document=? "
                "WHERE scope=? AND experience_id=?",
                (snapshot.version, document, scope, identifier),
            )
            self._connection.execute(
                "INSERT INTO experience_versions VALUES (?,?,?,?)",
                (scope, identifier, snapshot.version, document),
            )
            return snapshot

        return await self._run(update)

    def _get_version(self, scope: str, identifier: str, version: int | None) -> ExecutionExperience:
        if version is None:
            row = self._connection.execute(
                "SELECT version,document FROM experience_records WHERE scope=? AND experience_id=?",
                (scope, identifier),
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT version,document FROM experience_versions "
                "WHERE scope=? AND experience_id=? AND version=?",
                (scope, identifier, version),
            ).fetchone()
        if row is None:
            raise _not_found()
        return _sqlite_document(scope, identifier, row[0], row[1])

    async def get(
        self, scope: PayloadScope, experience_id: UUID, *, version: int | None = None
    ) -> ExecutionExperience:
        """Read a scoped version."""

        def get() -> ExecutionExperience:
            key = _sqlite_key(scope, experience_id)
            if version is not None:
                _positive_integer(version, "version")
            return self._get_version(*key, version)

        return await self._run(get)

    async def versions(
        self,
        scope: PayloadScope,
        experience_id: UUID,
        *,
        after_version: int = 0,
        limit: int = 50,
    ) -> Sequence[ExecutionExperience]:
        """Read a bounded page of the scoped version history."""

        def versions() -> tuple[ExecutionExperience, ...]:
            key = _sqlite_key(scope, experience_id)
            _check_after_version(after_version)
            _page_limit(limit)
            current = self._get_version(*key, None)
            if after_version >= current.version:
                return ()
            rows = self._connection.execute(
                "SELECT version,document FROM experience_versions "
                "WHERE scope=? AND experience_id=? AND version>? ORDER BY version LIMIT ?",
                (*key, after_version, limit),
            ).fetchall()
            return tuple(_sqlite_document(*key, row[0], row[1]) for row in rows)

        return await self._run(versions)

    async def query(
        self, scope: PayloadScope, *, limit: int = 50, cursor: UUID | None = None
    ) -> ExperiencePage:
        """Read a bounded insertion-order page of current versions."""

        def query() -> ExperiencePage:
            scope_key = _check_scope(scope).model_dump_json()
            _page_limit(limit)
            sequence = 0
            if cursor is not None:
                if not isinstance(cursor, UUID):
                    raise LearningValidationError("experience cursor must be a UUID")
                row = self._connection.execute(
                    "SELECT seq FROM experience_records WHERE scope=? AND experience_id=?",
                    (scope_key, str(cursor)),
                ).fetchone()
                if row is None:
                    raise LearningValidationError("experience cursor is not in the requested scope")
                sequence = row[0]
            rows = self._connection.execute(
                "SELECT experience_id,version,document FROM experience_records "
                "WHERE scope=? AND seq>? "
                "ORDER BY seq LIMIT ?",
                (scope_key, sequence, limit + 1),
            ).fetchall()
            page = tuple(_sqlite_document(scope_key, *row) for row in rows[:limit])
            return ExperiencePage(
                experiences=page,
                next_cursor=page[-1].experience_id if len(rows) > limit else None,
            )

        return await self._run(query)

    async def delete(self, scope: PayloadScope, experience_id: UUID) -> bool:
        """Atomically delete every scoped version of the experience."""

        def delete() -> bool:
            key = _sqlite_key(scope, experience_id)
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                "DELETE FROM experience_versions WHERE scope=? AND experience_id=?", key
            )
            return (
                self._connection.execute(
                    "DELETE FROM experience_records WHERE scope=? AND experience_id=?", key
                ).rowcount
                > 0
            )

        return await self._run(delete)

    async def health(self) -> None:
        """Check that the connection is usable."""
        await self._run(lambda: self._connection.execute("SELECT 1").fetchone())

    async def close(self) -> None:
        """Wait for an ongoing operation and close the connection idempotently."""
        await self._handle.close()


def _copy(experience: ExecutionExperience) -> ExecutionExperience:
    if not isinstance(experience, ExecutionExperience):
        raise TypeError("experience must be an ExecutionExperience")
    return ExecutionExperience.model_validate(experience.model_dump())


def _key(scope: PayloadScope, experience_id: UUID) -> PayloadScope:
    scope = _check_scope(scope)
    if not isinstance(experience_id, UUID):
        raise TypeError("experience_id must be a UUID")
    return scope


def _positive_integer(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LearningValidationError(f"{name} must be a positive integer")


def _validate_update(
    current: ExecutionExperience, snapshot: ExecutionExperience, expected_version: int
) -> None:
    if current.version != expected_version:
        raise ExperienceConflictError("experience version changed")
    if snapshot.version != current.version + 1:
        raise ExperienceConflictError("experience version must increase by one")
    if snapshot.created_at != current.created_at or snapshot.updated_at <= current.updated_at:
        raise ExperienceConflictError("experience timestamps are not consistent")
    if snapshot.execution_id != current.execution_id:
        raise ExperienceConflictError("execution identity cannot change")


def _check_after_version(after_version: int) -> None:
    if isinstance(after_version, bool) or not isinstance(after_version, int) or after_version < 0:
        raise LearningValidationError("after_version must be a nonnegative integer")


def _sqlite_key(scope: PayloadScope, experience_id: UUID) -> tuple[str, str]:
    return _key(scope, experience_id).model_dump_json(), str(experience_id)


def _sqlite_document(
    scope: str, identifier: str, version: int, document: str
) -> ExecutionExperience:
    snapshot = ExecutionExperience.model_validate_json(document)
    if (
        snapshot.scope.model_dump_json() != scope
        or str(snapshot.experience_id) != identifier
        or snapshot.version != version
    ):
        raise ExperienceStoreError("stored experience identity is inconsistent")
    return snapshot


def _page_limit(limit: int) -> None:
    _positive_integer(limit, "limit")
    if limit > 1000:
        raise LearningValidationError("limit must not exceed 1000")


def _not_found() -> ExperienceNotFoundError:
    return ExperienceNotFoundError("experience not found in the requested scope")


__all__ = [
    "ExperienceConflictError",
    "ExperienceNotFoundError",
    "ExperiencePage",
    "ExperienceRepositoryABC",
    "ExperienceStoreError",
    "InMemoryExperienceRepository",
    "SQLiteExperienceRepository",
]
