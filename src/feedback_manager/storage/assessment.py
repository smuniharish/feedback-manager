"""Scoped, versioned storage for independent feedback assessments."""

from __future__ import annotations

import asyncio
import threading
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from feedback_manager.assessment import AssessmentStatus, FeedbackAssessment, LearningDecision
from feedback_manager.learning.errors import LearningError, LearningValidationError
from feedback_manager.storage._sqlite import SQLiteHandle, open_with_cleanup
from feedback_manager.storage.experience import _check_after_version, _page_limit, _positive_integer
from feedback_manager.storage.payload import PayloadScope, _check_scope

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path


class AssessmentStoreError(LearningError):
    """Base class for assessment storage failures."""


class AssessmentNotFoundError(AssessmentStoreError, LookupError):
    """An assessment is not present in the requested scope."""


class AssessmentConflictError(AssessmentStoreError):
    """An assessment ID or expected version conflicts with the store."""


class AssessmentPage(BaseModel):
    """A bounded page of current assessments."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assessments: tuple[FeedbackAssessment, ...]
    next_cursor: UUID | None = None


class DecisionChange(BaseModel):
    """One recorded learning decision at an opaque, monotonically ordered position."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cursor: str
    feedback_id: UUID
    decision: LearningDecision


class DecisionChangePage(BaseModel):
    """A bounded page of the decision feed and the position after it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    changes: tuple[DecisionChange, ...]
    cursor: str | None = None
    has_more: bool = False


class AssessmentDeletion(BaseModel):
    """Every assessment version and decision removed for one experience."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assessments: tuple[FeedbackAssessment, ...] = ()
    decisions: tuple[LearningDecision, ...] = ()


class AssessmentRepositoryABC(ABC):
    """Persist independent assessments; the host authorizes the exact scope.

    References are not checked against feedback, experience or payload stores.
    Use `ExperiencePurger` for coordinated deletion of an experience's records.
    """

    durable: bool = False

    @abstractmethod
    async def create(self, assessment: FeedbackAssessment) -> FeedbackAssessment:
        """Create version one, or return the current version for an identical retry."""

    @abstractmethod
    async def update(
        self, assessment: FeedbackAssessment, *, expected_version: int
    ) -> FeedbackAssessment:
        """Append a version using optimistic concurrency control."""

    @abstractmethod
    async def get(
        self, scope: PayloadScope, assessment_id: UUID, *, version: int | None = None
    ) -> FeedbackAssessment:
        """Return a version or raise `AssessmentNotFoundError`."""

    @abstractmethod
    async def versions(
        self,
        scope: PayloadScope,
        assessment_id: UUID,
        *,
        after_version: int = 0,
        limit: int = 50,
    ) -> Sequence[FeedbackAssessment]:
        """Return up to `limit` versions in ascending order after the given version."""

    @abstractmethod
    async def for_feedback(
        self, scope: PayloadScope, feedback_id: UUID, *, limit: int = 50, cursor: UUID | None = None
    ) -> AssessmentPage:
        """Page current assessments of the feedback, without snapshot isolation."""

    @abstractmethod
    async def for_experience(
        self,
        scope: PayloadScope,
        experience_id: UUID,
        *,
        limit: int = 50,
        cursor: UUID | None = None,
    ) -> AssessmentPage:
        """Page current assessments of the experience, without snapshot isolation."""

    @abstractmethod
    async def decide(self, decision: LearningDecision) -> LearningDecision:
        """Record one decision for the current assessment version, idempotently."""

    @abstractmethod
    async def decision(
        self, scope: PayloadScope, assessment_id: UUID, *, assessment_version: int
    ) -> LearningDecision | None:
        """Return a version's decision; callers must separately check the current version."""

    @abstractmethod
    async def decision_changes(
        self, *, after: str | None = None, limit: int = 100
    ) -> DecisionChangePage:
        """Return decisions recorded after ``after`` in commit order, across all scopes.

        This is a host-level recovery feed. Consumers must re-resolve and verify
        scope for every entry; a decision deleted by a purge is no longer listed.
        """

    @abstractmethod
    async def delete_for_experience(
        self, scope: PayloadScope, experience_id: UUID
    ) -> AssessmentDeletion:
        """Delete every assessment version and decision for one scoped experience."""

    @abstractmethod
    async def health(self) -> None:
        """Check store availability."""


class InMemoryAssessmentRepository(AssessmentRepositoryABC):
    """Process-local assessment history, without durable or transactional guarantees.

    Work is offloaded from the event loop. Cancellation does not roll back an
    operation that has already started in a worker thread.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[PayloadScope, dict[UUID, list[FeedbackAssessment]]] = {}
        self._decisions: dict[tuple[PayloadScope, UUID, int], LearningDecision] = {}
        self._decision_ids: dict[tuple[PayloadScope, UUID], tuple[UUID, int]] = {}
        self._decision_log: dict[int, DecisionChange] = {}
        self._sequence = 0

    async def create(self, assessment: FeedbackAssessment) -> FeedbackAssessment:
        """Create or idempotently retrieve an assessment."""
        return await asyncio.to_thread(self._create, assessment)

    def _create(self, assessment: FeedbackAssessment) -> FeedbackAssessment:
        snapshot = _copy(assessment)
        if snapshot.version != 1:
            raise AssessmentConflictError("new assessments must be version 1")
        with self._lock:
            records = self._records.setdefault(snapshot.scope, {})
            history = records.get(snapshot.assessment_id)
            if history is not None:
                if history[0] == snapshot:
                    return _copy(history[-1])
                raise AssessmentConflictError("assessment ID already exists")
            records[snapshot.assessment_id] = [snapshot]
            return _copy(snapshot)

    async def update(
        self, assessment: FeedbackAssessment, *, expected_version: int
    ) -> FeedbackAssessment:
        """Append one version if the expected version is current."""
        return await asyncio.to_thread(self._update, assessment, expected_version)

    def _update(self, assessment: FeedbackAssessment, expected_version: int) -> FeedbackAssessment:
        _positive_integer(expected_version, "expected_version")
        snapshot = _copy(assessment)
        with self._lock:
            history = self._records.get(snapshot.scope, {}).get(snapshot.assessment_id)
            if history is None:
                raise _not_found()
            _validate_update(history[-1], snapshot, expected_version)
            history.append(snapshot)
            return _copy(snapshot)

    async def get(
        self, scope: PayloadScope, assessment_id: UUID, *, version: int | None = None
    ) -> FeedbackAssessment:
        """Return a scoped assessment version."""
        return await asyncio.to_thread(self._get, scope, assessment_id, version)

    def _get(
        self, scope: PayloadScope, assessment_id: UUID, version: int | None
    ) -> FeedbackAssessment:
        scope = _key(scope, assessment_id)
        if version is not None:
            _positive_integer(version, "version")
        with self._lock:
            history = self._records.get(scope, {}).get(assessment_id)
            if history is None or (version is not None and version > len(history)):
                raise _not_found()
            return _copy(history[-1] if version is None else history[version - 1])

    async def versions(
        self,
        scope: PayloadScope,
        assessment_id: UUID,
        *,
        after_version: int = 0,
        limit: int = 50,
    ) -> Sequence[FeedbackAssessment]:
        """Return a bounded page of scoped history."""
        return await asyncio.to_thread(self._versions, scope, assessment_id, after_version, limit)

    def _versions(
        self, scope: PayloadScope, assessment_id: UUID, after_version: int, limit: int
    ) -> Sequence[FeedbackAssessment]:
        scope = _key(scope, assessment_id)
        _check_after_version(after_version)
        _page_limit(limit)
        with self._lock:
            history = self._records.get(scope, {}).get(assessment_id)
            if history is None:
                raise _not_found()
            return tuple(_copy(item) for item in history[after_version : after_version + limit])

    async def for_feedback(
        self, scope: PayloadScope, feedback_id: UUID, *, limit: int = 50, cursor: UUID | None = None
    ) -> AssessmentPage:
        """Return a page of current assessments for one scoped feedback."""
        return await asyncio.to_thread(self._page, scope, "feedback_id", feedback_id, limit, cursor)

    async def for_experience(
        self,
        scope: PayloadScope,
        experience_id: UUID,
        *,
        limit: int = 50,
        cursor: UUID | None = None,
    ) -> AssessmentPage:
        """Return a page of current assessments for one scoped experience."""
        return await asyncio.to_thread(
            self._page, scope, "experience_id", experience_id, limit, cursor
        )

    def _page(
        self, scope: PayloadScope, field: str, value: UUID, limit: int, cursor: UUID | None
    ) -> AssessmentPage:
        scope = _key(scope, value)
        _page_limit(limit)
        if cursor is not None and not isinstance(cursor, UUID):
            raise LearningValidationError("assessment cursor must be a UUID")
        target = field.removesuffix("_id")
        with self._lock:
            records = self._records.get(scope, {})
            if cursor is not None and (
                cursor not in records or getattr(records[cursor][-1], field) != value
            ):
                raise LearningValidationError(f"assessment cursor is not in the requested {target}")
            after_cursor = cursor is None
            matches: list[FeedbackAssessment] = []
            for assessment_id, history in records.items():
                if not after_cursor:
                    if assessment_id == cursor:
                        after_cursor = True
                    continue
                if getattr(history[-1], field) == value:
                    matches.append(history[-1])
                    if len(matches) > limit:
                        break
            page = tuple(_copy(item) for item in matches[:limit])
            return AssessmentPage(
                assessments=page,
                next_cursor=page[-1].assessment_id if len(matches) > limit else None,
            )

    async def health(self) -> None:
        """The in-memory repository has no external dependency."""

    async def decide(self, decision: LearningDecision) -> LearningDecision:
        """Record an immutable decision against the current assessment version."""
        return await asyncio.to_thread(self._decide, decision)

    def _decide(self, decision: LearningDecision) -> LearningDecision:
        snapshot = _copy_decision(decision)
        key = (snapshot.scope, snapshot.assessment_id, snapshot.assessment_version)
        with self._lock:
            history = self._records.get(snapshot.scope, {}).get(snapshot.assessment_id)
            if history is None:
                raise _not_found()
            _validate_decision(history[-1], snapshot)
            previous = self._decisions.get(key)
            if previous is not None:
                if previous != snapshot:
                    raise AssessmentConflictError("assessment version already has a decision")
                return _copy_decision(previous)
            id_key = (snapshot.scope, snapshot.decision_id)
            if id_key in self._decision_ids:
                raise AssessmentConflictError("decision ID already exists")
            self._decisions[key] = snapshot
            self._decision_ids[id_key] = (snapshot.assessment_id, snapshot.assessment_version)
            self._sequence += 1
            self._decision_log[self._sequence] = DecisionChange(
                cursor=str(self._sequence), feedback_id=history[-1].feedback_id, decision=snapshot
            )
            return _copy_decision(snapshot)

    async def decision_changes(
        self, *, after: str | None = None, limit: int = 100
    ) -> DecisionChangePage:
        """Return a bounded page of the process-local decision feed."""
        return await asyncio.to_thread(self._decision_changes, after, limit)

    def _decision_changes(self, after: str | None, limit: int) -> DecisionChangePage:
        position = _feed_position(after)
        _page_limit(limit)
        with self._lock:
            changes = [
                change.model_copy(update={"decision": _copy_decision(change.decision)})
                for sequence, change in self._decision_log.items()
                if sequence > position
            ]
        return _feed_page(after, changes, limit)

    async def delete_for_experience(
        self, scope: PayloadScope, experience_id: UUID
    ) -> AssessmentDeletion:
        """Delete every scoped assessment of the experience and its decisions."""
        return await asyncio.to_thread(self._delete_for_experience, scope, experience_id)

    def _delete_for_experience(
        self, scope: PayloadScope, experience_id: UUID
    ) -> AssessmentDeletion:
        scope = _key(scope, experience_id)
        with self._lock:
            records = self._records.get(scope, {})
            removed = [
                assessment_id
                for assessment_id, history in records.items()
                if history[0].experience_id == experience_id
            ]
            assessments: list[FeedbackAssessment] = []
            decisions: list[LearningDecision] = []
            for assessment_id in removed:
                history = records.pop(assessment_id)
                assessments.extend(history)
                for version in range(1, len(history) + 1):
                    decision = self._decisions.pop((scope, assessment_id, version), None)
                    if decision is not None:
                        decisions.append(decision)
                        del self._decision_ids[(scope, decision.decision_id)]
            removed_ids = {decision.decision_id for decision in decisions}
            for sequence, change in list(self._decision_log.items()):
                if change.decision.scope == scope and change.decision.decision_id in removed_ids:
                    del self._decision_log[sequence]
        return AssessmentDeletion(assessments=tuple(assessments), decisions=tuple(decisions))

    async def decision(
        self, scope: PayloadScope, assessment_id: UUID, *, assessment_version: int
    ) -> LearningDecision | None:
        """Read a decision for a scoped assessment version."""
        return await asyncio.to_thread(self._decision, scope, assessment_id, assessment_version)

    def _decision(
        self, scope: PayloadScope, assessment_id: UUID, assessment_version: int
    ) -> LearningDecision | None:
        scope = _key(scope, assessment_id)
        _positive_integer(assessment_version, "assessment_version")
        with self._lock:
            history = self._records.get(scope, {}).get(assessment_id)
            if history is None or assessment_version > len(history):
                raise _not_found()
            found = self._decisions.get((scope, assessment_id, assessment_version))
            return None if found is None else _copy_decision(found)


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS assessment_records (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    assessment_id TEXT NOT NULL,
    feedback_id TEXT NOT NULL,
    experience_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    document TEXT NOT NULL,
    UNIQUE (scope, assessment_id)
);
CREATE INDEX IF NOT EXISTS assessment_records_feedback
ON assessment_records (scope, feedback_id, seq);
CREATE INDEX IF NOT EXISTS assessment_records_experience
ON assessment_records (scope, experience_id, seq);
CREATE TABLE IF NOT EXISTS assessment_versions (
    scope TEXT NOT NULL,
    assessment_id TEXT NOT NULL,
    feedback_id TEXT NOT NULL,
    experience_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    document TEXT NOT NULL,
    PRIMARY KEY (scope, assessment_id, version)
);
CREATE TABLE IF NOT EXISTS assessment_decisions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    assessment_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    decision_id TEXT NOT NULL,
    feedback_id TEXT NOT NULL,
    document TEXT NOT NULL,
    UNIQUE (scope, assessment_id, version),
    UNIQUE (scope, decision_id)
);
"""


class SQLiteAssessmentRepository(AssessmentRepositoryABC):
    """Durable single-host assessment archive with transactional CAS.

    Use `open` and `close` to avoid blocking the event loop. SQLite serializes
    writers across connections. Cancellation does not roll back an operation
    that has already started in a worker thread.
    """

    durable = True

    def __init__(self, path: str | Path) -> None:
        self._handle = SQLiteHandle(
            path, _SQLITE_SCHEMA, name="assessment", error_type=AssessmentStoreError
        )
        self._connection = self._handle.connection

    @classmethod
    async def open(cls, path: str | Path) -> SQLiteAssessmentRepository:
        """Open a local archive without blocking the event loop."""
        return await open_with_cleanup(lambda: cls(path), lambda repository: repository.close())

    async def _run[R](self, operation: Callable[[], R]) -> R:
        return await self._handle.run(operation)

    async def create(self, assessment: FeedbackAssessment) -> FeedbackAssessment:
        """Atomically create version one or return the current version for a retry."""

        def create() -> FeedbackAssessment:
            snapshot = _copy(assessment)
            if snapshot.version != 1:
                raise AssessmentConflictError("new assessments must be version 1")
            scope, identifier = _sqlite_key(snapshot.scope, snapshot.assessment_id)
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT feedback_id,experience_id,version,document FROM assessment_versions "
                "WHERE scope=? AND assessment_id=? AND version=1",
                (scope, identifier),
            ).fetchone()
            if row is not None:
                if _sqlite_document(scope, identifier, *row) != snapshot:
                    raise AssessmentConflictError("assessment ID already exists")
                return self._get_version(scope, identifier, None)
            values = (
                scope,
                identifier,
                str(snapshot.feedback_id),
                str(snapshot.experience_id),
                1,
                snapshot.model_dump_json(),
            )
            self._connection.execute(
                "INSERT INTO assessment_records "
                "(scope,assessment_id,feedback_id,experience_id,version,document) "
                "VALUES (?,?,?,?,?,?)",
                values,
            )
            self._connection.execute("INSERT INTO assessment_versions VALUES (?,?,?,?,?,?)", values)
            return snapshot

        return await self._run(create)

    async def update(
        self, assessment: FeedbackAssessment, *, expected_version: int
    ) -> FeedbackAssessment:
        """Atomically append the next version if the expected version is current."""

        def update() -> FeedbackAssessment:
            _positive_integer(expected_version, "expected_version")
            snapshot = _copy(assessment)
            scope, identifier = _sqlite_key(snapshot.scope, snapshot.assessment_id)
            self._connection.execute("BEGIN IMMEDIATE")
            current = self._get_version(scope, identifier, None)
            _validate_update(current, snapshot, expected_version)
            document = snapshot.model_dump_json()
            self._connection.execute(
                "UPDATE assessment_records SET version=?,document=? "
                "WHERE scope=? AND assessment_id=?",
                (snapshot.version, document, scope, identifier),
            )
            self._connection.execute(
                "INSERT INTO assessment_versions VALUES (?,?,?,?,?,?)",
                (
                    scope,
                    identifier,
                    str(snapshot.feedback_id),
                    str(snapshot.experience_id),
                    snapshot.version,
                    document,
                ),
            )
            return snapshot

        return await self._run(update)

    def _get_version(self, scope: str, identifier: str, version: int | None) -> FeedbackAssessment:
        if version is None:
            row = self._connection.execute(
                "SELECT feedback_id,experience_id,version,document FROM assessment_records "
                "WHERE scope=? AND assessment_id=?",
                (scope, identifier),
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT feedback_id,experience_id,version,document FROM assessment_versions "
                "WHERE scope=? AND assessment_id=? AND version=?",
                (scope, identifier, version),
            ).fetchone()
        if row is None:
            raise _not_found()
        return _sqlite_document(scope, identifier, *row)

    async def get(
        self, scope: PayloadScope, assessment_id: UUID, *, version: int | None = None
    ) -> FeedbackAssessment:
        """Read a scoped assessment version."""

        def get() -> FeedbackAssessment:
            key = _sqlite_key(scope, assessment_id)
            if version is not None:
                _positive_integer(version, "version")
            return self._get_version(*key, version)

        return await self._run(get)

    async def versions(
        self,
        scope: PayloadScope,
        assessment_id: UUID,
        *,
        after_version: int = 0,
        limit: int = 50,
    ) -> Sequence[FeedbackAssessment]:
        """Read a bounded page of the scoped version history."""

        def versions() -> tuple[FeedbackAssessment, ...]:
            key = _sqlite_key(scope, assessment_id)
            _check_after_version(after_version)
            _page_limit(limit)
            current = self._get_version(*key, None)
            if after_version >= current.version:
                return ()
            rows = self._connection.execute(
                "SELECT feedback_id,experience_id,version,document FROM assessment_versions "
                "WHERE scope=? AND assessment_id=? AND version>? ORDER BY version LIMIT ?",
                (*key, after_version, limit),
            ).fetchall()
            return tuple(_sqlite_document(*key, *row) for row in rows)

        return await self._run(versions)

    async def for_feedback(
        self, scope: PayloadScope, feedback_id: UUID, *, limit: int = 50, cursor: UUID | None = None
    ) -> AssessmentPage:
        """Read a scoped insertion-order page for one feedback."""
        return await self._page(scope, "feedback_id", feedback_id, limit, cursor)

    async def for_experience(
        self,
        scope: PayloadScope,
        experience_id: UUID,
        *,
        limit: int = 50,
        cursor: UUID | None = None,
    ) -> AssessmentPage:
        """Read a scoped insertion-order page for one experience."""
        return await self._page(scope, "experience_id", experience_id, limit, cursor)

    async def _page(
        self, scope: PayloadScope, column: str, value: UUID, limit: int, cursor: UUID | None
    ) -> AssessmentPage:
        # ``column`` is one of two fixed identifiers chosen by this class.
        def query() -> AssessmentPage:
            scope_key = _key(scope, value).model_dump_json()
            _page_limit(limit)
            sequence = 0
            if cursor is not None:
                if not isinstance(cursor, UUID):
                    raise LearningValidationError("assessment cursor must be a UUID")
                row = self._connection.execute(
                    f"SELECT seq FROM assessment_records "  # noqa: S608
                    f"WHERE scope=? AND {column}=? AND assessment_id=?",
                    (scope_key, str(value), str(cursor)),
                ).fetchone()
                if row is None:
                    raise LearningValidationError(
                        f"assessment cursor is not in the requested {column.removesuffix('_id')}"
                    )
                sequence = row[0]
            rows = self._connection.execute(
                "SELECT assessment_id,feedback_id,experience_id,version,document "  # noqa: S608
                f"FROM assessment_records WHERE scope=? AND {column}=? AND seq>? "
                "ORDER BY seq LIMIT ?",
                (scope_key, str(value), sequence, limit + 1),
            ).fetchall()
            page = tuple(_sqlite_document(scope_key, *row) for row in rows[:limit])
            return AssessmentPage(
                assessments=page,
                next_cursor=page[-1].assessment_id if len(rows) > limit else None,
            )

        return await self._run(query)

    async def health(self) -> None:
        """Check that the connection is usable."""
        await self._run(lambda: self._connection.execute("SELECT 1").fetchone())

    async def decide(self, decision: LearningDecision) -> LearningDecision:
        """Atomically decide the current version or return an identical decision."""

        def decide() -> LearningDecision:
            snapshot = _copy_decision(decision)
            scope, identifier = _sqlite_key(snapshot.scope, snapshot.assessment_id)
            self._connection.execute("BEGIN IMMEDIATE")
            current = self._get_version(scope, identifier, None)
            _validate_decision(current, snapshot)
            row = self._connection.execute(
                "SELECT decision_id,document FROM assessment_decisions "
                "WHERE scope=? AND assessment_id=? AND version=?",
                (scope, identifier, snapshot.assessment_version),
            ).fetchone()
            if row is not None:
                previous = _sqlite_decision(scope, identifier, snapshot.assessment_version, *row)
                if previous != snapshot:
                    raise AssessmentConflictError("assessment version already has a decision")
                return previous
            existing_id = self._connection.execute(
                "SELECT 1 FROM assessment_decisions WHERE scope=? AND decision_id=?",
                (scope, str(snapshot.decision_id)),
            ).fetchone()
            if existing_id is not None:
                raise AssessmentConflictError("decision ID already exists")
            self._connection.execute(
                "INSERT INTO assessment_decisions "
                "(scope,assessment_id,version,decision_id,feedback_id,document) "
                "VALUES (?,?,?,?,?,?)",
                (
                    scope,
                    identifier,
                    snapshot.assessment_version,
                    str(snapshot.decision_id),
                    str(current.feedback_id),
                    snapshot.model_dump_json(),
                ),
            )
            return snapshot

        return await self._run(decide)

    async def decision_changes(
        self, *, after: str | None = None, limit: int = 100
    ) -> DecisionChangePage:
        """Read decisions in commit order; SQLite serializes writers."""

        def changes() -> DecisionChangePage:
            position = _feed_position(after)
            _page_limit(limit)
            rows = self._connection.execute(
                "SELECT seq,feedback_id,scope,assessment_id,version,decision_id,document "
                "FROM assessment_decisions WHERE seq>? ORDER BY seq LIMIT ?",
                (position, limit + 1),
            ).fetchall()
            return _feed_page(
                after,
                [
                    DecisionChange(
                        cursor=str(row[0]),
                        feedback_id=UUID(row[1]),
                        decision=_sqlite_decision(*row[2:]),
                    )
                    for row in rows
                ],
                limit,
            )

        return await self._run(changes)

    async def delete_for_experience(
        self, scope: PayloadScope, experience_id: UUID
    ) -> AssessmentDeletion:
        """Atomically delete the experience's scoped assessments and decisions."""

        def delete() -> AssessmentDeletion:
            scope_key = _key(scope, experience_id).model_dump_json()
            self._connection.execute("BEGIN IMMEDIATE")
            identifiers = [
                row[0]
                for row in self._connection.execute(
                    "SELECT assessment_id FROM assessment_records "
                    "WHERE scope=? AND experience_id=? ORDER BY seq",
                    (scope_key, str(experience_id)),
                ).fetchall()
            ]
            assessments: list[FeedbackAssessment] = []
            decisions: list[LearningDecision] = []
            for identifier in identifiers:
                rows = self._connection.execute(
                    "SELECT feedback_id,experience_id,version,document FROM assessment_versions "
                    "WHERE scope=? AND assessment_id=? ORDER BY version",
                    (scope_key, identifier),
                ).fetchall()
                assessments.extend(_sqlite_document(scope_key, identifier, *row) for row in rows)
                rows = self._connection.execute(
                    "SELECT version,decision_id,document FROM assessment_decisions "
                    "WHERE scope=? AND assessment_id=? ORDER BY version",
                    (scope_key, identifier),
                ).fetchall()
                decisions.extend(_sqlite_decision(scope_key, identifier, *row) for row in rows)
                for table in ("assessment_decisions", "assessment_versions", "assessment_records"):
                    self._connection.execute(
                        f"DELETE FROM {table} WHERE scope=? AND assessment_id=?",  # noqa: S608
                        (scope_key, identifier),
                    )
            return AssessmentDeletion(assessments=tuple(assessments), decisions=tuple(decisions))

        return await self._run(delete)

    async def decision(
        self, scope: PayloadScope, assessment_id: UUID, *, assessment_version: int
    ) -> LearningDecision | None:
        """Read a decision for a scoped assessment version."""

        def decision() -> LearningDecision | None:
            key = _sqlite_key(scope, assessment_id)
            _positive_integer(assessment_version, "assessment_version")
            self._get_version(*key, assessment_version)
            row = self._connection.execute(
                "SELECT decision_id,document FROM assessment_decisions "
                "WHERE scope=? AND assessment_id=? AND version=?",
                (*key, assessment_version),
            ).fetchone()
            return None if row is None else _sqlite_decision(*key, assessment_version, *row)

        return await self._run(decision)

    async def close(self) -> None:
        """Wait for an ongoing operation and close the connection idempotently."""
        await self._handle.close()


def _copy(assessment: FeedbackAssessment) -> FeedbackAssessment:
    if not isinstance(assessment, FeedbackAssessment):
        raise TypeError("assessment must be a FeedbackAssessment")
    return FeedbackAssessment.model_validate(assessment.model_dump())


def _copy_decision(decision: LearningDecision) -> LearningDecision:
    if not isinstance(decision, LearningDecision):
        raise TypeError("decision must be a LearningDecision")
    return LearningDecision.model_validate(decision.model_dump())


def _key(scope: PayloadScope, identifier: UUID) -> PayloadScope:
    scope = _check_scope(scope)
    if not isinstance(identifier, UUID):
        raise TypeError("assessment identifier must be a UUID")
    return scope


def _validate_update(
    current: FeedbackAssessment, snapshot: FeedbackAssessment, expected_version: int
) -> None:
    if current.version != expected_version:
        raise AssessmentConflictError("assessment version changed")
    if snapshot.version != current.version + 1:
        raise AssessmentConflictError("assessment version must increase by one")
    if (
        snapshot.feedback_id != current.feedback_id
        or snapshot.experience_id != current.experience_id
    ):
        raise AssessmentConflictError("assessment source identity cannot change")
    if snapshot.assessed_at <= current.assessed_at:
        raise AssessmentConflictError("assessment time must increase")


def _validate_decision(assessment: FeedbackAssessment, decision: LearningDecision) -> None:
    if decision.assessment_version != assessment.version:
        raise AssessmentConflictError("assessment version changed")
    if decision.accepted and assessment.status is not AssessmentStatus.SUPPORTED:
        raise AssessmentConflictError("only supported feedback can be accepted for learning")
    if decision.accepted and not any(
        item.experience_id != assessment.experience_id
        and item.feedback_id != assessment.feedback_id
        for item in assessment.supporting_evidence
    ):
        raise AssessmentConflictError("accepted feedback needs independent supporting evidence")
    if decision.decided_at < assessment.assessed_at:
        raise AssessmentConflictError("decision time must not precede the assessment")


def _sqlite_key(scope: PayloadScope, identifier: UUID) -> tuple[str, str]:
    return _key(scope, identifier).model_dump_json(), str(identifier)


def _sqlite_document(
    scope: str, identifier: str, feedback_id: str, experience_id: str, version: int, document: str
) -> FeedbackAssessment:
    snapshot = FeedbackAssessment.model_validate_json(document)
    if (
        snapshot.scope.model_dump_json() != scope
        or str(snapshot.assessment_id) != identifier
        or str(snapshot.feedback_id) != feedback_id
        or str(snapshot.experience_id) != experience_id
        or snapshot.version != version
    ):
        raise AssessmentStoreError("stored assessment identity is inconsistent")
    return snapshot


def _sqlite_decision(
    scope: str, identifier: str, version: int, decision_id: str, document: str
) -> LearningDecision:
    snapshot = LearningDecision.model_validate_json(document)
    if (
        snapshot.scope.model_dump_json() != scope
        or str(snapshot.assessment_id) != identifier
        or snapshot.assessment_version != version
        or str(snapshot.decision_id) != decision_id
    ):
        raise AssessmentStoreError("stored learning decision identity is inconsistent")
    return snapshot


def _feed_position(after: str | None) -> int:
    if after is None:
        return 0
    if not isinstance(after, str) or not after.isascii() or not after.isdigit():
        raise LearningValidationError("decision feed cursor is invalid")
    return int(after)


def _feed_page(
    after: str | None, changes: Sequence[DecisionChange], limit: int
) -> DecisionChangePage:
    page = tuple(changes[:limit])
    return DecisionChangePage(
        changes=page,
        cursor=page[-1].cursor if page else after,
        has_more=len(changes) > limit,
    )


def _not_found() -> AssessmentNotFoundError:
    return AssessmentNotFoundError("assessment not found in the requested scope")


__all__ = [
    "AssessmentConflictError",
    "AssessmentDeletion",
    "AssessmentNotFoundError",
    "AssessmentPage",
    "AssessmentRepositoryABC",
    "AssessmentStoreError",
    "DecisionChange",
    "DecisionChangePage",
    "InMemoryAssessmentRepository",
    "SQLiteAssessmentRepository",
]
