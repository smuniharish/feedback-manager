"""A process-local, tenant-scoped artifact repository."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING
from uuid import UUID

from feedback_manager.learning.errors import (
    ArtifactConflictError,
    ArtifactNotFoundError,
    LearningValidationError,
)
from feedback_manager.learning.models import ArtifactStatus, LearnedArtifact
from feedback_manager.learning.scope import is_applicable
from feedback_manager.storage.artifacts.base import ArtifactPage, ArtifactQuery, ArtifactRepository
from feedback_manager.storage.artifacts.validation import validate_update

if TYPE_CHECKING:
    from collections.abc import Sequence

    from feedback_manager.learning.models import TenantScope

type _ScopeKey = tuple[str, str]


def _copy(artifact: LearnedArtifact) -> LearnedArtifact:
    return LearnedArtifact.model_validate(artifact.model_dump())


def _not_found(artifact_id: UUID) -> ArtifactNotFoundError:
    return ArtifactNotFoundError(
        "artifact not found in the requested scope", artifact_id=artifact_id
    )


class InMemoryArtifactRepository(ArtifactRepository):
    """Keep immutable artifact versions in memory until the process exits.

    Operations share a lock and can be called from multiple tasks, threads, and
    event loops. Snapshots are copied on both writes and reads because Pydantic's
    frozen models do not recursively freeze nested JSON objects.
    """

    def __init__(self) -> None:
        self._scopes: dict[_ScopeKey, dict[UUID, list[LearnedArtifact]]] = {}
        self._lock = threading.Lock()

    async def create(self, artifact: LearnedArtifact) -> LearnedArtifact:
        """Create a candidate, or return an identical previously stored candidate."""
        snapshot = _copy(artifact)
        if snapshot.version != 1 or snapshot.status is not ArtifactStatus.CANDIDATE:
            raise ArtifactConflictError("new artifacts must be version 1 candidates")
        key = (snapshot.scope.tenant_id, snapshot.scope.namespace)
        with self._lock:
            artifacts = self._scopes.setdefault(key, {})
            history = artifacts.get(snapshot.artifact_id)
            if history is not None:
                if history[0] == snapshot:
                    return _copy(history[-1])
                raise ArtifactConflictError(
                    "artifact ID already exists", artifact_id=snapshot.artifact_id
                )
            artifacts[snapshot.artifact_id] = [snapshot]
            return _copy(snapshot)

    async def update(self, artifact: LearnedArtifact, *, expected_version: int) -> LearnedArtifact:
        """Append a validated snapshot using optimistic concurrency control."""
        if (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 1
        ):
            raise LearningValidationError("expected_version must be a positive integer")
        snapshot = _copy(artifact)
        key = (snapshot.scope.tenant_id, snapshot.scope.namespace)
        with self._lock:
            history = self._scopes.get(key, {}).get(snapshot.artifact_id)
            if history is None:
                raise _not_found(snapshot.artifact_id)
            current = history[-1]
            if current.version != expected_version:
                raise ArtifactConflictError(
                    "artifact version changed",
                    artifact_id=snapshot.artifact_id,
                    current_version=current.version,
                    expected_version=expected_version,
                )
            validate_update(history, snapshot)
            history.append(snapshot)
            return _copy(snapshot)

    async def get(
        self, scope: TenantScope, artifact_id: UUID, *, version: int | None = None
    ) -> LearnedArtifact:
        """Return a scoped version, raising if it does not exist."""
        if version is not None and (
            isinstance(version, bool) or not isinstance(version, int) or version < 1
        ):
            raise LearningValidationError("version must be a positive integer")
        with self._lock:
            history = self._scopes.get((scope.tenant_id, scope.namespace), {}).get(artifact_id)
            if history is None or (version is not None and version > len(history)):
                raise _not_found(artifact_id)
            return _copy(history[-1] if version is None else history[version - 1])

    async def get_many(
        self, scope: TenantScope, artifact_ids: Sequence[UUID]
    ) -> Sequence[LearnedArtifact]:
        """Return current artifacts in input order, rejecting any missing ID."""
        with self._lock:
            artifacts = self._scopes.get((scope.tenant_id, scope.namespace), {})
            results: list[LearnedArtifact] = []
            for artifact_id in artifact_ids:
                history = artifacts.get(artifact_id)
                if history is None:
                    raise _not_found(artifact_id)
                results.append(_copy(history[-1]))
            return results

    async def query(self, query: ArtifactQuery) -> ArtifactPage:
        """Return a page in insertion order, using an artifact ID as its cursor."""
        cursor = None
        if query.cursor is not None:
            try:
                cursor = UUID(query.cursor)
            except ValueError as exc:
                raise LearningValidationError("invalid artifact cursor") from exc
        with self._lock:
            artifacts = self._scopes.get((query.scope.tenant_id, query.scope.namespace), {})
            after_cursor = cursor is None
            matches: list[LearnedArtifact] = []
            for artifact_id, history in artifacts.items():
                if not after_cursor:
                    if artifact_id == cursor:
                        after_cursor = True
                    continue
                current = history[-1]
                if query.statuses and current.status not in query.statuses:
                    continue
                if query.artifact_types and current.artifact_type not in query.artifact_types:
                    continue
                if (
                    query.source_feedback_id is not None
                    and query.source_feedback_id not in current.source_feedback_ids
                ):
                    continue
                if query.enforce_visibility and not is_applicable(
                    current.scope,
                    query.scope,
                    agent_id=query.agent_id,
                    agent_type=query.agent_type,
                    task_type=query.task_type,
                    tool=query.tool,
                    workflow=query.workflow,
                ):
                    continue
                matches.append(current)
                if len(matches) > query.limit:
                    break
            if not after_cursor:
                raise LearningValidationError("artifact cursor is not in the requested scope")
            has_more = len(matches) > query.limit
            page = tuple(_copy(item) for item in matches[: query.limit])
            return ArtifactPage(
                artifacts=page, next_cursor=str(page[-1].artifact_id) if has_more else None
            )

    async def versions(self, scope: TenantScope, artifact_id: UUID) -> Sequence[LearnedArtifact]:
        """Return all scoped versions in ascending order."""
        with self._lock:
            history = self._scopes.get((scope.tenant_id, scope.namespace), {}).get(artifact_id)
            if history is None:
                raise _not_found(artifact_id)
            return tuple(_copy(item) for item in history)

    async def health(self) -> None:
        """The in-memory repository has no external dependency to check."""


__all__ = ["InMemoryArtifactRepository"]
