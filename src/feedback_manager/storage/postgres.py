"""PostgreSQL experience, assessment and payload stores: ``feedback-manager[postgres]``.

Each store uses a psycopg ``AsyncConnectionPool``. Pass your own pool to share it
(the store never closes it) or a DSN for a store-owned pool. ``open`` creates the
tables idempotently under an advisory lock, so several processes may start at
once. Table names are ``<table_prefix><name>``; the prefix is validated and
always quoted as an identifier.

Writes run in single transactions with row locks, giving the same
compare-and-set guarantees across processes as the SQLite stores give on one
host. Decision inserts are serialized with a transaction-scoped advisory lock,
so the decision feed position order equals commit order and a reader never
skips a decision committed later at a lower position.

psycopg's async mode needs a selector event loop, which is not the Windows
default.
"""

from __future__ import annotations

try:
    from psycopg import errors, sql
    from psycopg_pool import AsyncConnectionPool
except ImportError as exc:  # pragma: no cover - exercised in a subprocess test
    raise ImportError(
        "feedback_manager.storage.postgres needs psycopg; "
        'install it with: pip install "feedback-manager[postgres]"'
    ) from exc

import hashlib
import re
from typing import TYPE_CHECKING, Any, ClassVar, LiteralString, Self
from uuid import UUID

from feedback_manager.assessment import FeedbackAssessment, LearningDecision
from feedback_manager.experience import ExecutionExperience
from feedback_manager.learning.errors import LearningError, LearningValidationError
from feedback_manager.storage.assessment import (
    AssessmentConflictError,
    AssessmentDeletion,
    AssessmentNotFoundError,
    AssessmentPage,
    AssessmentRepositoryABC,
    AssessmentStoreError,
    DecisionChange,
    DecisionChangePage,
    _copy_decision,
    _feed_page,
    _feed_position,
    _validate_decision,
)
from feedback_manager.storage.assessment import _copy as _copy_assessment
from feedback_manager.storage.assessment import _validate_update as _validate_assessment_update
from feedback_manager.storage.experience import (
    ExperienceConflictError,
    ExperienceNotFoundError,
    ExperiencePage,
    ExperienceRepositoryABC,
    ExperienceStoreError,
    _check_after_version,
    _page_limit,
    _positive_integer,
    _validate_update,
)
from feedback_manager.storage.experience import _copy as _copy_experience
from feedback_manager.storage.payload import (
    PayloadCodec,
    PayloadMetadata,
    PayloadNotFoundError,
    PayloadScope,
    PayloadStoreABC,
    PayloadStoreError,
    _check_key,
    _check_scope,
    _decode,
    _encode,
    _identical,
    _prepare,
    _row_key,
    _stored,
    payload_scope_key,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from psycopg import AsyncConnection

_PREFIX = re.compile(r"[a-z_][a-z0-9_]{0,39}")


class _PostgresStore:
    """Pool ownership, schema creation and error translation shared by the stores."""

    durable = True
    _error: ClassVar[type[LearningError]]
    _tables: ClassVar[tuple[str, ...]]
    _schema: ClassVar[LiteralString]

    def __init__(self, pool: AsyncConnectionPool, *, table_prefix: str = "fm_") -> None:
        if not isinstance(pool, AsyncConnectionPool):
            raise TypeError("pool must be a psycopg_pool.AsyncConnectionPool")
        if not isinstance(table_prefix, str) or _PREFIX.fullmatch(table_prefix) is None:
            raise ValueError("table_prefix must match [a-z_][a-z0-9_]{0,39}")
        self._pool = pool
        self._owns_pool = False
        self._prefix = table_prefix
        self._names = {name: sql.Identifier(f"{table_prefix}{name}") for name in self._tables}
        digest = hashlib.sha256(f"feedback-manager:{table_prefix}".encode()).digest()
        self._lock_key = int.from_bytes(digest[:8], "big", signed=True)

    @classmethod
    async def open(
        cls,
        pool: AsyncConnectionPool | str,
        *,
        table_prefix: str = "fm_",
        max_connections: int = 10,
        connect_timeout: float = 10.0,
        **options: Any,
    ) -> Self:
        """Open a store on a caller-owned pool or a DSN, and create its tables.

        A pool created from a DSN is owned and closed by `close`.
        """
        if isinstance(pool, str):
            owned = AsyncConnectionPool(
                pool,
                min_size=1,
                max_size=max_connections,
                timeout=connect_timeout,
                open=False,
                kwargs={"autocommit": True},
            )
            try:
                await owned.open(wait=True, timeout=connect_timeout)
                store = cls(owned, table_prefix=table_prefix, **options)
                store._owns_pool = True
                await store.create_schema()
            except BaseException:
                await owned.close()
                raise
            return store
        store = cls(pool, table_prefix=table_prefix, **options)
        await store.create_schema()
        return store

    async def create_schema(self) -> None:
        """Create this store's tables and indexes if they do not exist."""

        async def create(connection: AsyncConnection[Any]) -> None:
            await connection.execute("SELECT pg_advisory_xact_lock(%s)", (self._lock_key,))
            await connection.execute(sql.SQL(self._schema).format(**self._identifiers()))

        await self._transaction(create)

    async def drop_schema(self) -> None:
        """Drop this store's tables; intended for tests and decommissioning."""

        async def drop(connection: AsyncConnection[Any]) -> None:
            for name in reversed(self._tables):
                await connection.execute(
                    sql.SQL("DROP TABLE IF EXISTS {}").format(self._names[name])
                )

        await self._transaction(drop)

    async def health(self) -> None:
        """Check that the database is reachable."""

        async def check(connection: AsyncConnection[Any]) -> None:
            await connection.execute("SELECT 1")

        await self._transaction(check)

    async def close(self) -> None:
        """Close the pool if this store owns it; an injected pool is left open."""
        if self._owns_pool:
            await self._pool.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    def _identifiers(self) -> dict[str, sql.Composable]:
        identifiers: dict[str, sql.Composable] = dict(self._names)
        identifiers.update(
            {
                f"{name}_{suffix}": sql.Identifier(f"{self._prefix}{name}_{suffix}")
                for name in self._tables
                for suffix in ("idx", "idx2")
            }
        )
        return identifiers

    def _sql(self, statement: LiteralString) -> sql.Composed:
        return sql.SQL(statement).format(**self._names)

    async def _transaction[R](self, operation: Callable[[AsyncConnection[Any]], Awaitable[R]]) -> R:
        try:
            async with self._pool.connection() as connection, connection.transaction():
                return await operation(connection)
        except LearningError:
            raise
        except errors.Error as exc:
            raise self._error(f"PostgreSQL operation failed ({type(exc).__name__})") from exc

    @staticmethod
    async def _one(
        connection: AsyncConnection[Any], statement: sql.Composed, params: Sequence[Any]
    ) -> tuple[Any, ...] | None:
        cursor = await connection.execute(statement, params)
        return await cursor.fetchone()

    @staticmethod
    async def _all(
        connection: AsyncConnection[Any], statement: sql.Composed, params: Sequence[Any]
    ) -> list[tuple[Any, ...]]:
        cursor = await connection.execute(statement, params)
        return await cursor.fetchall()


class PostgresPayloadStore(_PostgresStore, PayloadStoreABC):
    """Durable, multi-process payload archive; an injected codec is caller-owned."""

    _error = PayloadStoreError
    _tables = ("payloads",)
    _schema = """
    CREATE TABLE IF NOT EXISTS {payloads} (
        scope TEXT NOT NULL,
        payload_id UUID NOT NULL,
        metadata TEXT NOT NULL,
        data BYTEA NOT NULL,
        PRIMARY KEY (scope, payload_id)
    );
    """

    def __init__(
        self,
        pool: AsyncConnectionPool,
        *,
        table_prefix: str = "fm_",
        codec: PayloadCodec | None = None,
    ) -> None:
        if codec is not None and not isinstance(codec, PayloadCodec):
            raise TypeError("codec must be a PayloadCodec")
        super().__init__(pool, table_prefix=table_prefix)
        self._codec = codec

    async def write(
        self,
        scope: PayloadScope,
        data: bytes,
        *,
        content_type: str,
        serialization_version: str,
        payload_id: UUID | None = None,
    ) -> PayloadMetadata:
        """Write one payload, deduplicating identical writes by scope and ID."""
        record = await _prepare(
            scope,
            data,
            content_type=content_type,
            serialization_version=serialization_version,
            payload_id=payload_id,
        )
        key = _row_key(record.scope, record.payload_id)
        existing = await self._transaction(lambda connection: self._fetch(connection, key))
        if existing is not None:
            return _identical(existing[0], record)
        encoded = await _encode(self._codec, record.scope, data)

        async def insert(connection: AsyncConnection[Any]) -> PayloadMetadata:
            await connection.execute(
                self._sql(
                    "INSERT INTO {payloads} (scope,payload_id,metadata,data) "
                    "VALUES (%s,%s,%s,%s) ON CONFLICT (scope,payload_id) DO NOTHING"
                ),
                (key[0], record.payload_id, record.model_dump_json(), encoded),
            )
            stored = await self._fetch(connection, key)
            if stored is None:  # pragma: no cover - the row was just inserted or already existed
                raise PayloadStoreError("payload write was not persisted")
            return _identical(stored[0], record)

        return await self._transaction(insert)

    async def read(self, scope: PayloadScope, payload_id: UUID) -> bytes:
        """Read logical bytes and verify their size and digest."""
        record, encoded = await self._get(scope, payload_id)
        return await _decode(self._codec, record, encoded)

    async def metadata(self, scope: PayloadScope, payload_id: UUID) -> PayloadMetadata:
        """Return the scoped payload metadata."""
        return (await self._get(scope, payload_id))[0]

    async def delete(self, scope: PayloadScope, payload_id: UUID) -> bool:
        """Delete the payload only from the supplied scope."""
        key = _row_key(_check_key(scope, payload_id), payload_id)

        async def delete(connection: AsyncConnection[Any]) -> bool:
            cursor = await connection.execute(
                self._sql("DELETE FROM {payloads} WHERE scope=%s AND payload_id=%s"),
                (key[0], payload_id),
            )
            return cursor.rowcount > 0

        return await self._transaction(delete)

    async def _get(self, scope: PayloadScope, payload_id: UUID) -> tuple[PayloadMetadata, bytes]:
        key = _row_key(_check_key(scope, payload_id), payload_id)
        record = await self._transaction(lambda connection: self._fetch(connection, key))
        if record is None:
            raise PayloadNotFoundError("payload not found in the requested scope")
        return record

    async def _fetch(
        self, connection: AsyncConnection[Any], key: tuple[str, str]
    ) -> tuple[PayloadMetadata, bytes] | None:
        row = await self._one(
            connection,
            self._sql("SELECT metadata,data FROM {payloads} WHERE scope=%s AND payload_id=%s"),
            (key[0], UUID(key[1])),
        )
        return None if row is None else _stored(key, row[0], row[1])


class PostgresExperienceRepository(_PostgresStore, ExperienceRepositoryABC):
    """Durable, multi-process experience archive with row-locked version appends."""

    _error = ExperienceStoreError
    _tables = ("experiences", "experience_versions")
    _schema = """
    CREATE TABLE IF NOT EXISTS {experiences} (
        position BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
        scope TEXT NOT NULL,
        experience_id UUID NOT NULL,
        version INTEGER NOT NULL,
        document TEXT NOT NULL,
        PRIMARY KEY (scope, experience_id)
    );
    CREATE INDEX IF NOT EXISTS {experiences_idx} ON {experiences} (scope, position);
    CREATE TABLE IF NOT EXISTS {experience_versions} (
        scope TEXT NOT NULL,
        experience_id UUID NOT NULL,
        version INTEGER NOT NULL,
        document TEXT NOT NULL,
        PRIMARY KEY (scope, experience_id, version)
    );
    """

    async def create(self, experience: ExecutionExperience) -> ExecutionExperience:
        """Atomically create version one or return the current version for a retry."""
        snapshot = _copy_experience(experience)
        if snapshot.version != 1:
            raise ExperienceConflictError("new experiences must be version 1")
        scope = payload_scope_key(snapshot.scope)
        identifier = snapshot.experience_id
        document = snapshot.model_dump_json()

        async def create(connection: AsyncConnection[Any]) -> ExecutionExperience:
            inserted = await self._one(
                connection,
                self._sql(
                    "INSERT INTO {experiences} (scope,experience_id,version,document) "
                    "VALUES (%s,%s,1,%s) ON CONFLICT (scope,experience_id) DO NOTHING "
                    "RETURNING experience_id"
                ),
                (scope, identifier, document),
            )
            if inserted is not None:
                await connection.execute(
                    self._sql("INSERT INTO {experience_versions} VALUES (%s,%s,1,%s)"),
                    (scope, identifier, document),
                )
                return snapshot
            if await self._version(connection, scope, identifier, 1) != snapshot:
                raise ExperienceConflictError("experience ID already exists")
            return await self._version(connection, scope, identifier, None)

        return await self._transaction(create)

    async def update(
        self, experience: ExecutionExperience, *, expected_version: int
    ) -> ExecutionExperience:
        """Atomically append the next version if the expected version is current."""
        _positive_integer(expected_version, "expected_version")
        snapshot = _copy_experience(experience)
        scope = payload_scope_key(snapshot.scope)
        identifier = snapshot.experience_id

        async def update(connection: AsyncConnection[Any]) -> ExecutionExperience:
            current = await self._version(connection, scope, identifier, None, lock=True)
            _validate_update(current, snapshot, expected_version)
            document = snapshot.model_dump_json()
            await connection.execute(
                self._sql(
                    "UPDATE {experiences} SET version=%s,document=%s "
                    "WHERE scope=%s AND experience_id=%s"
                ),
                (snapshot.version, document, scope, identifier),
            )
            await connection.execute(
                self._sql("INSERT INTO {experience_versions} VALUES (%s,%s,%s,%s)"),
                (scope, identifier, snapshot.version, document),
            )
            return snapshot

        return await self._transaction(update)

    async def get(
        self, scope: PayloadScope, experience_id: UUID, *, version: int | None = None
    ) -> ExecutionExperience:
        """Read a scoped version."""
        key = _scope_key(scope, experience_id)
        if version is not None:
            _positive_integer(version, "version")
        return await self._transaction(
            lambda connection: self._version(connection, key, experience_id, version)
        )

    async def versions(
        self,
        scope: PayloadScope,
        experience_id: UUID,
        *,
        after_version: int = 0,
        limit: int = 50,
    ) -> Sequence[ExecutionExperience]:
        """Read a bounded page of the scoped version history."""
        key = _scope_key(scope, experience_id)
        _check_after_version(after_version)
        _page_limit(limit)

        async def versions(connection: AsyncConnection[Any]) -> tuple[ExecutionExperience, ...]:
            await self._version(connection, key, experience_id, None)
            rows = await self._all(
                connection,
                self._sql(
                    "SELECT version,document FROM {experience_versions} "
                    "WHERE scope=%s AND experience_id=%s AND version>%s "
                    "ORDER BY version LIMIT %s"
                ),
                (key, experience_id, after_version, limit),
            )
            return tuple(_experience(key, experience_id, *row) for row in rows)

        return await self._transaction(versions)

    async def query(
        self, scope: PayloadScope, *, limit: int = 50, cursor: UUID | None = None
    ) -> ExperiencePage:
        """Read a bounded insertion-order page of current versions."""
        key = payload_scope_key(_check_scope(scope))
        _page_limit(limit)
        if cursor is not None and not isinstance(cursor, UUID):
            raise LearningValidationError("experience cursor must be a UUID")

        async def query(connection: AsyncConnection[Any]) -> ExperiencePage:
            position = 0
            if cursor is not None:
                row = await self._one(
                    connection,
                    self._sql(
                        "SELECT position FROM {experiences} WHERE scope=%s AND experience_id=%s"
                    ),
                    (key, cursor),
                )
                if row is None:
                    raise LearningValidationError("experience cursor is not in the requested scope")
                position = row[0]
            rows = await self._all(
                connection,
                self._sql(
                    "SELECT experience_id,version,document FROM {experiences} "
                    "WHERE scope=%s AND position>%s ORDER BY position LIMIT %s"
                ),
                (key, position, limit + 1),
            )
            page = tuple(_experience(key, *row) for row in rows[:limit])
            return ExperiencePage(
                experiences=page,
                next_cursor=page[-1].experience_id if len(rows) > limit else None,
            )

        return await self._transaction(query)

    async def delete(self, scope: PayloadScope, experience_id: UUID) -> bool:
        """Atomically delete every scoped version of the experience."""
        key = _scope_key(scope, experience_id)

        async def delete(connection: AsyncConnection[Any]) -> bool:
            # Deleting the current row first waits for a concurrent update, so the
            # version delete then also sees any version that update appended.
            cursor = await connection.execute(
                self._sql("DELETE FROM {experiences} WHERE scope=%s AND experience_id=%s"),
                (key, experience_id),
            )
            await connection.execute(
                self._sql("DELETE FROM {experience_versions} WHERE scope=%s AND experience_id=%s"),
                (key, experience_id),
            )
            return cursor.rowcount > 0

        return await self._transaction(delete)

    async def _version(
        self,
        connection: AsyncConnection[Any],
        scope: str,
        identifier: UUID,
        version: int | None,
        *,
        lock: bool = False,
    ) -> ExecutionExperience:
        if version is None:
            statement = (
                "SELECT version,document FROM {experiences} WHERE scope=%s AND experience_id=%s"
            )
            row = await self._one(
                connection,
                self._sql(statement + " FOR UPDATE" if lock else statement),
                (scope, identifier),
            )
        else:
            row = await self._one(
                connection,
                self._sql(
                    "SELECT version,document FROM {experience_versions} "
                    "WHERE scope=%s AND experience_id=%s AND version=%s"
                ),
                (scope, identifier, version),
            )
        if row is None:
            raise ExperienceNotFoundError("experience not found in the requested scope")
        return _experience(scope, identifier, *row)


class PostgresAssessmentRepository(_PostgresStore, AssessmentRepositoryABC):
    """Durable, multi-process assessment archive with a commit-ordered decision feed."""

    _error = AssessmentStoreError
    _tables = ("assessments", "assessment_versions", "assessment_decisions")
    _schema = """
    CREATE TABLE IF NOT EXISTS {assessments} (
        position BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
        scope TEXT NOT NULL,
        assessment_id UUID NOT NULL,
        feedback_id UUID NOT NULL,
        experience_id UUID NOT NULL,
        version INTEGER NOT NULL,
        document TEXT NOT NULL,
        PRIMARY KEY (scope, assessment_id)
    );
    CREATE INDEX IF NOT EXISTS {assessments_idx} ON {assessments} (scope, feedback_id, position);
    CREATE INDEX IF NOT EXISTS {assessments_idx2}
        ON {assessments} (scope, experience_id, position);
    CREATE TABLE IF NOT EXISTS {assessment_versions} (
        scope TEXT NOT NULL,
        assessment_id UUID NOT NULL,
        version INTEGER NOT NULL,
        document TEXT NOT NULL,
        PRIMARY KEY (scope, assessment_id, version)
    );
    CREATE TABLE IF NOT EXISTS {assessment_decisions} (
        position BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
        scope TEXT NOT NULL,
        assessment_id UUID NOT NULL,
        version INTEGER NOT NULL,
        decision_id UUID NOT NULL,
        feedback_id UUID NOT NULL,
        document TEXT NOT NULL,
        PRIMARY KEY (scope, assessment_id, version),
        UNIQUE (scope, decision_id)
    );
    """

    async def create(self, assessment: FeedbackAssessment) -> FeedbackAssessment:
        """Atomically create version one or return the current version for a retry."""
        snapshot = _copy_assessment(assessment)
        if snapshot.version != 1:
            raise AssessmentConflictError("new assessments must be version 1")
        scope = payload_scope_key(snapshot.scope)
        identifier = snapshot.assessment_id
        document = snapshot.model_dump_json()

        async def create(connection: AsyncConnection[Any]) -> FeedbackAssessment:
            inserted = await self._one(
                connection,
                self._sql(
                    "INSERT INTO {assessments} "
                    "(scope,assessment_id,feedback_id,experience_id,version,document) "
                    "VALUES (%s,%s,%s,%s,1,%s) ON CONFLICT (scope,assessment_id) DO NOTHING "
                    "RETURNING assessment_id"
                ),
                (scope, identifier, snapshot.feedback_id, snapshot.experience_id, document),
            )
            if inserted is not None:
                await connection.execute(
                    self._sql("INSERT INTO {assessment_versions} VALUES (%s,%s,1,%s)"),
                    (scope, identifier, document),
                )
                return snapshot
            if await self._version(connection, scope, identifier, 1) != snapshot:
                raise AssessmentConflictError("assessment ID already exists")
            return await self._version(connection, scope, identifier, None)

        return await self._transaction(create)

    async def update(
        self, assessment: FeedbackAssessment, *, expected_version: int
    ) -> FeedbackAssessment:
        """Atomically append the next version if the expected version is current."""
        _positive_integer(expected_version, "expected_version")
        snapshot = _copy_assessment(assessment)
        scope = payload_scope_key(snapshot.scope)
        identifier = snapshot.assessment_id

        async def update(connection: AsyncConnection[Any]) -> FeedbackAssessment:
            current = await self._version(connection, scope, identifier, None, lock=True)
            _validate_assessment_update(current, snapshot, expected_version)
            document = snapshot.model_dump_json()
            await connection.execute(
                self._sql(
                    "UPDATE {assessments} SET version=%s,document=%s "
                    "WHERE scope=%s AND assessment_id=%s"
                ),
                (snapshot.version, document, scope, identifier),
            )
            await connection.execute(
                self._sql("INSERT INTO {assessment_versions} VALUES (%s,%s,%s,%s)"),
                (scope, identifier, snapshot.version, document),
            )
            return snapshot

        return await self._transaction(update)

    async def get(
        self, scope: PayloadScope, assessment_id: UUID, *, version: int | None = None
    ) -> FeedbackAssessment:
        """Read a scoped version."""
        key = _scope_key(scope, assessment_id)
        if version is not None:
            _positive_integer(version, "version")
        return await self._transaction(
            lambda connection: self._version(connection, key, assessment_id, version)
        )

    async def versions(
        self,
        scope: PayloadScope,
        assessment_id: UUID,
        *,
        after_version: int = 0,
        limit: int = 50,
    ) -> Sequence[FeedbackAssessment]:
        """Read a bounded page of the scoped version history."""
        key = _scope_key(scope, assessment_id)
        _check_after_version(after_version)
        _page_limit(limit)

        async def versions(connection: AsyncConnection[Any]) -> tuple[FeedbackAssessment, ...]:
            await self._version(connection, key, assessment_id, None)
            rows = await self._all(
                connection,
                self._sql(
                    "SELECT version,document FROM {assessment_versions} "
                    "WHERE scope=%s AND assessment_id=%s AND version>%s "
                    "ORDER BY version LIMIT %s"
                ),
                (key, assessment_id, after_version, limit),
            )
            return tuple(_assessment(key, assessment_id, *row) for row in rows)

        return await self._transaction(versions)

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

    async def decide(self, decision: LearningDecision) -> LearningDecision:
        """Atomically decide the current version or return an identical decision."""
        snapshot = _copy_decision(decision)
        scope = payload_scope_key(_check_scope(snapshot.scope))
        identifier = snapshot.assessment_id

        async def decide(connection: AsyncConnection[Any]) -> LearningDecision:
            current = await self._version(connection, scope, identifier, None, lock=True)
            _validate_decision(current, snapshot)
            row = await self._one(
                connection,
                self._sql(
                    "SELECT document FROM {assessment_decisions} "
                    "WHERE scope=%s AND assessment_id=%s AND version=%s"
                ),
                (scope, identifier, snapshot.assessment_version),
            )
            if row is not None:
                previous = _decision(scope, row[0])
                if previous != snapshot:
                    raise AssessmentConflictError("assessment version already has a decision")
                return previous
            # Serialize feed inserts so positions are assigned in commit order.
            await connection.execute("SELECT pg_advisory_xact_lock(%s)", (self._lock_key,))
            try:
                await connection.execute(
                    self._sql(
                        "INSERT INTO {assessment_decisions} "
                        "(scope,assessment_id,version,decision_id,feedback_id,document) "
                        "VALUES (%s,%s,%s,%s,%s,%s)"
                    ),
                    (
                        scope,
                        identifier,
                        snapshot.assessment_version,
                        snapshot.decision_id,
                        current.feedback_id,
                        snapshot.model_dump_json(),
                    ),
                )
            except errors.UniqueViolation as exc:
                raise AssessmentConflictError("decision ID already exists") from exc
            return snapshot

        return await self._transaction(decide)

    async def decision(
        self, scope: PayloadScope, assessment_id: UUID, *, assessment_version: int
    ) -> LearningDecision | None:
        """Read a decision for a scoped assessment version."""
        key = _scope_key(scope, assessment_id)
        _positive_integer(assessment_version, "assessment_version")

        async def decision(connection: AsyncConnection[Any]) -> LearningDecision | None:
            await self._version(connection, key, assessment_id, assessment_version)
            row = await self._one(
                connection,
                self._sql(
                    "SELECT document FROM {assessment_decisions} "
                    "WHERE scope=%s AND assessment_id=%s AND version=%s"
                ),
                (key, assessment_id, assessment_version),
            )
            return None if row is None else _decision(key, row[0], assessment_id)

        return await self._transaction(decision)

    async def decision_changes(
        self, *, after: str | None = None, limit: int = 100
    ) -> DecisionChangePage:
        """Read decisions in commit order across all scopes."""
        position = _feed_position(after)
        _page_limit(limit)

        async def changes(connection: AsyncConnection[Any]) -> DecisionChangePage:
            rows = await self._all(
                connection,
                self._sql(
                    "SELECT position,feedback_id,scope,document FROM {assessment_decisions} "
                    "WHERE position>%s ORDER BY position LIMIT %s"
                ),
                (position, limit + 1),
            )
            return _feed_page(
                after,
                [
                    DecisionChange(
                        cursor=str(row[0]), feedback_id=row[1], decision=_decision(row[2], row[3])
                    )
                    for row in rows
                ],
                limit,
            )

        return await self._transaction(changes)

    async def delete_for_experience(
        self, scope: PayloadScope, experience_id: UUID
    ) -> AssessmentDeletion:
        """Atomically delete the experience's scoped assessments and decisions."""
        key = _scope_key(scope, experience_id)

        async def delete(connection: AsyncConnection[Any]) -> AssessmentDeletion:
            identifiers = [
                row[0]
                for row in await self._all(
                    connection,
                    self._sql(
                        "SELECT assessment_id FROM {assessments} "
                        "WHERE scope=%s AND experience_id=%s ORDER BY position FOR UPDATE"
                    ),
                    (key, experience_id),
                )
            ]
            assessments: list[FeedbackAssessment] = []
            decisions: list[LearningDecision] = []
            for identifier in identifiers:
                rows = await self._all(
                    connection,
                    self._sql(
                        "SELECT version,document FROM {assessment_versions} "
                        "WHERE scope=%s AND assessment_id=%s ORDER BY version"
                    ),
                    (key, identifier),
                )
                assessments.extend(_assessment(key, identifier, *row) for row in rows)
                rows = await self._all(
                    connection,
                    self._sql(
                        "DELETE FROM {assessment_decisions} WHERE scope=%s AND assessment_id=%s "
                        "RETURNING version,document"
                    ),
                    (key, identifier),
                )
                decisions.extend(
                    _decision(key, row[1], identifier)
                    for row in sorted(rows, key=lambda row: row[0])
                )
                for table in ("assessment_versions", "assessments"):
                    await connection.execute(
                        sql.SQL("DELETE FROM {} WHERE scope=%s AND assessment_id=%s").format(
                            self._names[table]
                        ),
                        (key, identifier),
                    )
            return AssessmentDeletion(assessments=tuple(assessments), decisions=tuple(decisions))

        return await self._transaction(delete)

    async def _page(
        self, scope: PayloadScope, column: str, value: UUID, limit: int, cursor: UUID | None
    ) -> AssessmentPage:
        key = _scope_key(scope, value)
        _page_limit(limit)
        if cursor is not None and not isinstance(cursor, UUID):
            raise LearningValidationError("assessment cursor must be a UUID")
        field = sql.Identifier(column)

        async def query(connection: AsyncConnection[Any]) -> AssessmentPage:
            position = 0
            if cursor is not None:
                row = await self._one(
                    connection,
                    sql.SQL(
                        "SELECT position FROM {table} "
                        "WHERE scope=%s AND {field}=%s AND assessment_id=%s"
                    ).format(table=self._names["assessments"], field=field),
                    (key, value, cursor),
                )
                if row is None:
                    raise LearningValidationError(
                        f"assessment cursor is not in the requested {column.removesuffix('_id')}"
                    )
                position = row[0]
            rows = await self._all(
                connection,
                sql.SQL(
                    "SELECT assessment_id,version,document FROM {table} "
                    "WHERE scope=%s AND {field}=%s AND position>%s ORDER BY position LIMIT %s"
                ).format(table=self._names["assessments"], field=field),
                (key, value, position, limit + 1),
            )
            page = tuple(_assessment(key, *row) for row in rows[:limit])
            return AssessmentPage(
                assessments=page,
                next_cursor=page[-1].assessment_id if len(rows) > limit else None,
            )

        return await self._transaction(query)

    async def _version(
        self,
        connection: AsyncConnection[Any],
        scope: str,
        identifier: UUID,
        version: int | None,
        *,
        lock: bool = False,
    ) -> FeedbackAssessment:
        if version is None:
            statement = (
                "SELECT version,document FROM {assessments} WHERE scope=%s AND assessment_id=%s"
            )
            row = await self._one(
                connection,
                self._sql(statement + " FOR UPDATE" if lock else statement),
                (scope, identifier),
            )
        else:
            row = await self._one(
                connection,
                self._sql(
                    "SELECT version,document FROM {assessment_versions} "
                    "WHERE scope=%s AND assessment_id=%s AND version=%s"
                ),
                (scope, identifier, version),
            )
        if row is None:
            raise AssessmentNotFoundError("assessment not found in the requested scope")
        return _assessment(scope, identifier, *row)


def _scope_key(scope: PayloadScope, identifier: UUID) -> str:
    scope = _check_scope(scope)
    if not isinstance(identifier, UUID):
        raise TypeError("identifier must be a UUID")
    return payload_scope_key(scope)


def _experience(scope: str, identifier: UUID, version: int, document: str) -> ExecutionExperience:
    snapshot = ExecutionExperience.model_validate_json(document)
    if (
        payload_scope_key(snapshot.scope) != scope
        or snapshot.experience_id != identifier
        or snapshot.version != version
    ):
        raise ExperienceStoreError("stored experience identity is inconsistent")
    return snapshot


def _assessment(scope: str, identifier: UUID, version: int, document: str) -> FeedbackAssessment:
    snapshot = FeedbackAssessment.model_validate_json(document)
    if (
        payload_scope_key(snapshot.scope) != scope
        or snapshot.assessment_id != identifier
        or snapshot.version != version
    ):
        raise AssessmentStoreError("stored assessment identity is inconsistent")
    return snapshot


def _decision(scope: str, document: str, identifier: UUID | None = None) -> LearningDecision:
    snapshot = LearningDecision.model_validate_json(document)
    if payload_scope_key(snapshot.scope) != scope or (
        identifier is not None and snapshot.assessment_id != identifier
    ):
        raise AssessmentStoreError("stored learning decision identity is inconsistent")
    return snapshot


__all__ = [
    "PostgresAssessmentRepository",
    "PostgresExperienceRepository",
    "PostgresPayloadStore",
]
