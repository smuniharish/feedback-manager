"""Application-supplied durable artifact repository for single-host deployments.

This example uses SQLite WAL, atomic version appends and shared validation rules.
It is not a distributed publication/outbox implementation. Keep the database on
a local filesystem and use application-managed backups and retention.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from typing import TYPE_CHECKING

from feedback_manager.learning import (
    ArtifactConflictError,
    ArtifactNotFoundError,
    ArtifactStatus,
    LearnedArtifact,
    LearningValidationError,
)
from feedback_manager.storage.artifacts import ArtifactPage, ArtifactQuery, ArtifactRepository
from feedback_manager.storage.artifacts.base import ProjectionIntent
from feedback_manager.storage.artifacts.validation import validate_update

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path
    from uuid import UUID

    from feedback_manager.learning import TenantScope

_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL, namespace TEXT NOT NULL, artifact_id TEXT NOT NULL,
    version INTEGER NOT NULL, status TEXT NOT NULL, artifact_type TEXT NOT NULL,
    document TEXT NOT NULL,
    UNIQUE (tenant_id, namespace, artifact_id)
);
CREATE INDEX IF NOT EXISTS artifacts_lookup
ON artifacts (tenant_id, namespace, status, artifact_type);
CREATE TABLE IF NOT EXISTS artifact_versions (
    tenant_id TEXT NOT NULL, namespace TEXT NOT NULL, artifact_id TEXT NOT NULL,
    version INTEGER NOT NULL, document TEXT NOT NULL,
    PRIMARY KEY (tenant_id, namespace, artifact_id, version)
);
CREATE TABLE IF NOT EXISTS projection_intents (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL, namespace TEXT NOT NULL,
    document TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS projection_intents_scope
ON projection_intents (tenant_id, namespace, seq);
"""


class SQLiteArtifactRepository(ArtifactRepository):
    """Persist immutable artifact history with optimistic, transactional updates."""

    durable = True
    atomic_projection_intents = True

    def __init__(self, path: str | Path) -> None:
        self._connection = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def _keys(self, scope: TenantScope, artifact_id: UUID) -> tuple[str, str, str]:
        return scope.tenant_id, scope.namespace, str(artifact_id)

    async def _run[R](self, operation: Callable[[], R]) -> R:
        def locked() -> R:
            with self._lock, self._connection:
                return operation()

        return await asyncio.to_thread(locked)

    def _history(self, scope: TenantScope, artifact_id: UUID) -> tuple[LearnedArtifact, ...]:
        rows = self._connection.execute(
            "SELECT document FROM artifact_versions WHERE tenant_id=? AND namespace=? "
            "AND artifact_id=? ORDER BY version",
            self._keys(scope, artifact_id),
        ).fetchall()
        return tuple(LearnedArtifact.model_validate_json(row[0]) for row in rows)

    def _write(self, artifact: LearnedArtifact, *, create: bool) -> None:
        document = artifact.model_dump_json()
        keys = self._keys(artifact.scope, artifact.artifact_id)
        values = (artifact.version, artifact.status.value, artifact.artifact_type.value, document)
        if create:
            self._connection.execute(
                "INSERT INTO artifacts (tenant_id,namespace,artifact_id,version,status,"
                "artifact_type,document) VALUES (?,?,?,?,?,?,?)",
                (*keys, *values),
            )
        else:
            self._connection.execute(
                "UPDATE artifacts SET version=?,status=?,artifact_type=?,document=? "
                "WHERE tenant_id=? AND namespace=? AND artifact_id=?",
                (*values, *keys),
            )
        self._connection.execute(
            "INSERT INTO artifact_versions VALUES (?,?,?,?,?)",
            (*keys, artifact.version, document),
        )

    async def create(self, artifact: LearnedArtifact) -> LearnedArtifact:
        """Create a candidate, deduplicating identical first versions."""
        snapshot = LearnedArtifact.model_validate(artifact.model_dump())
        if snapshot.version != 1 or snapshot.status is not ArtifactStatus.CANDIDATE:
            raise ArtifactConflictError("new artifacts must be version 1 candidates")

        def create() -> LearnedArtifact:
            self._connection.execute("BEGIN IMMEDIATE")
            history = self._history(snapshot.scope, snapshot.artifact_id)
            if history:
                if history[0] != snapshot:
                    raise ArtifactConflictError("artifact ID already exists")
                return history[-1]
            self._write(snapshot, create=True)
            return snapshot

        return await self._run(create)

    async def update(self, artifact: LearnedArtifact, *, expected_version: int) -> LearnedArtifact:
        """Atomically validate and append one version, rejecting concurrent changes."""
        return await self._update(artifact, expected_version=expected_version, project=False)

    async def update_with_projection(
        self, artifact: LearnedArtifact, *, expected_version: int
    ) -> LearnedArtifact:
        """Commit a version and its projection outbox intent in one transaction."""
        return await self._update(artifact, expected_version=expected_version, project=True)

    async def _update(
        self, artifact: LearnedArtifact, *, expected_version: int, project: bool
    ) -> LearnedArtifact:
        if isinstance(expected_version, bool) or expected_version < 1:
            raise LearningValidationError("expected_version must be a positive integer")
        snapshot = LearnedArtifact.model_validate(artifact.model_dump())

        def update() -> LearnedArtifact:
            self._connection.execute("BEGIN IMMEDIATE")
            history = self._history(snapshot.scope, snapshot.artifact_id)
            if not history:
                raise ArtifactNotFoundError("artifact not found")
            if history[-1].version != expected_version:
                raise ArtifactConflictError(
                    "artifact version changed",
                    current_version=history[-1].version,
                    expected_version=expected_version,
                )
            validate_update(history, snapshot)
            self._write(snapshot, create=False)
            if project:
                intent = ProjectionIntent.for_artifact(snapshot)
                self._connection.execute(
                    "INSERT INTO projection_intents (intent_id,tenant_id,namespace,document) "
                    "VALUES (?,?,?,?)",
                    (
                        str(intent.intent_id),
                        intent.scope.tenant_id,
                        intent.scope.namespace,
                        intent.model_dump_json(),
                    ),
                )
            return snapshot

        return await self._run(update)

    async def pending_projections(
        self, scope: TenantScope, *, limit: int = 100
    ) -> Sequence[ProjectionIntent]:
        """Read a bounded durable outbox page; use one projector for this scope."""
        if not 1 <= limit <= 1000:
            raise LearningValidationError("projection limit must be between 1 and 1000")

        def read() -> tuple[ProjectionIntent, ...]:
            rows = self._connection.execute(
                "SELECT document FROM projection_intents WHERE tenant_id=? AND namespace=? "
                "ORDER BY seq LIMIT ?",
                (scope.tenant_id, scope.namespace, limit),
            ).fetchall()
            return tuple(ProjectionIntent.model_validate_json(row[0]) for row in rows)

        return await self._run(read)

    async def acknowledge_projection(self, scope: TenantScope, intent_id: UUID) -> None:
        """Acknowledge one scope-qualified intent idempotently."""
        await self._run(
            lambda: self._connection.execute(
                "DELETE FROM projection_intents WHERE tenant_id=? AND namespace=? AND intent_id=?",
                (scope.tenant_id, scope.namespace, str(intent_id)),
            )
        )

    async def get(
        self, scope: TenantScope, artifact_id: UUID, *, version: int | None = None
    ) -> LearnedArtifact:
        """Read a scoped version, raising for missing IDs or versions."""
        if version is not None and (isinstance(version, bool) or version < 1):
            raise LearningValidationError("version must be a positive integer")

        def get() -> LearnedArtifact:
            statement = (
                "SELECT document FROM artifact_versions WHERE tenant_id=? AND namespace=? "
                "AND artifact_id=?"
            )
            arguments: tuple[object, ...] = self._keys(scope, artifact_id)
            if version is not None:
                statement += " AND version=?"
                arguments = (*arguments, version)
            row = self._connection.execute(
                statement + " ORDER BY version DESC LIMIT 1", arguments
            ).fetchone()
            if row is None:
                raise ArtifactNotFoundError("artifact not found in the requested scope")
            return LearnedArtifact.model_validate_json(row[0])

        return await self._run(get)

    async def get_many(
        self, scope: TenantScope, artifact_ids: Sequence[UUID]
    ) -> Sequence[LearnedArtifact]:
        """Read current versions in request order, rejecting any missing ID."""
        return tuple([await self.get(scope, identifier) for identifier in artifact_ids])

    async def versions(self, scope: TenantScope, artifact_id: UUID) -> Sequence[LearnedArtifact]:
        """Return the immutable history in version order."""
        history = await self._run(lambda: self._history(scope, artifact_id))
        if not history:
            raise ArtifactNotFoundError("artifact not found")
        return history

    async def query(self, query: ArtifactQuery) -> ArtifactPage:
        """Use bound SQL filters and a bounded insertion-order keyset page."""
        if query.cursor is not None and not query.cursor.isdecimal():
            raise LearningValidationError("invalid artifact cursor")
        filters = ["tenant_id=?", "namespace=?", "seq>?"]
        args: list[object] = [
            query.scope.tenant_id,
            query.scope.namespace,
            int(query.cursor or "0"),
        ]
        for field, values in (("status", query.statuses), ("artifact_type", query.artifact_types)):
            if values:
                filters.append(f"{field} IN ({','.join('?' for _ in values)})")
                args.extend(value.value for value in values)
        if query.source_feedback_id is not None:
            filters.append(
                "EXISTS (SELECT 1 FROM json_each(document,'$.evidence') AS e "
                "WHERE json_extract(e.value,'$.feedback_id')=?)"
            )
            args.append(str(query.source_feedback_id))
        if query.enforce_visibility:
            filters.append(
                "(json_extract(document,'$.scope.visibility')<>'agent' "
                "OR json_extract(document,'$.scope.agent_id')=?)"
            )
            args.append(query.agent_id)
            for field in ("agent_type", "task_type", "tool", "workflow"):
                filters.append(
                    f"(json_extract(document,'$.scope.{field}') IS NULL "
                    f"OR json_extract(document,'$.scope.{field}')=?)"
                )
                args.append(getattr(query, field))
        statement = (
            "SELECT seq,document FROM artifacts WHERE "  # noqa: S608 - static clauses; values bound
            + " AND ".join(filters)
            + " ORDER BY seq LIMIT ?"
        )
        args.append(query.limit + 1)

        def read() -> ArtifactPage:
            rows = self._connection.execute(statement, args).fetchall()
            page = rows[: query.limit]
            return ArtifactPage(
                artifacts=tuple(LearnedArtifact.model_validate_json(row[1]) for row in page),
                next_cursor=str(page[-1][0]) if len(rows) > query.limit else None,
            )

        return await self._run(read)

    async def health(self) -> None:
        """Check the database connection."""
        await self._run(lambda: self._connection.execute("SELECT 1").fetchone())

    async def close(self) -> None:
        """Close the application-owned connection after its workers have stopped."""
        with self._lock:
            self._connection.close()
