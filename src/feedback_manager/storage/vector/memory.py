"""An in-memory vector store for tests, examples, and single-process use."""

from __future__ import annotations

import asyncio
import math
from typing import TYPE_CHECKING

from feedback_manager.storage.vector.base import (
    VectorCapabilities,
    VectorMatch,
    VectorRecord,
    VectorStore,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from feedback_manager.core._types import JsonObject
    from feedback_manager.embeddings.models import EmbeddingSpace
    from feedback_manager.learning.models import TenantScope

_Key = tuple[str, str, str, str]


def cosine_distance(left: Sequence[float], right: Sequence[float]) -> float:
    """Return ``1 - cosine similarity``, clamped to be non-negative.

    Args:
        left: The first vector.
        right: The second vector, with the same length as ``left``.

    Returns:
        The cosine distance, or ``1.0`` when either vector has zero length.
    """
    left_norm = math.sqrt(math.fsum(value * value for value in left))
    right_norm = math.sqrt(math.fsum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 1.0
    dot = math.fsum(a * b for a, b in zip(left, right, strict=True))
    return max(0.0, 1.0 - dot / (left_norm * right_norm))


def validate_query(space: EmbeddingSpace, vector: Sequence[float], limit: int) -> None:
    """Reject a query vector or limit that cannot be searched in ``space``.

    Raises:
        ValueError: If ``vector`` does not have ``space.dimensions`` values or
            ``limit`` is less than one.
    """
    if len(vector) != space.dimensions:
        raise ValueError(
            f"query vector has {len(vector)} dimensions; the embedding space has {space.dimensions}"
        )
    if limit < 1:
        raise ValueError("limit must be at least 1")


def metadata_matches(record_metadata: JsonObject, expected: JsonObject | None) -> bool:
    """Return whether every expected key is present with an equal value."""
    if not expected:
        return True
    return all(
        key in record_metadata and record_metadata[key] == value for key, value in expected.items()
    )


class InMemoryVectorStore(VectorStore):
    """Keep vector projections in process memory with exact cosine search.

    Records are keyed by tenant, namespace, embedding space, and record ID, so
    the same record ID can exist independently in different scopes and spaces.
    Search is brute force, filters by scope, space, and metadata before ranking,
    and is therefore exact. All operations are serialized by an
    `asyncio.Lock`.
    """

    def __init__(self) -> None:
        """Create an empty store."""
        self._records: dict[_Key, VectorRecord] = {}
        self._lock = asyncio.Lock()

    @property
    def capabilities(self) -> VectorCapabilities:
        """Describe optional operations supported by this adapter."""
        return VectorCapabilities(metadata_filtering=True, index_management=False)

    @staticmethod
    def _key(scope: TenantScope, space: EmbeddingSpace, record_id: str) -> _Key:
        return (scope.tenant_id, scope.namespace, space.space_id, record_id)

    async def upsert_many(self, records: Sequence[VectorRecord]) -> None:
        """Idempotently insert or replace a batch of projections.

        Raises:
            ValueError: If a record's vector does not match its space dimensions.
        """
        for record in records:
            if len(record.vector) != record.space.dimensions:
                raise ValueError("vector dimensions do not match the embedding space")
        async with self._lock:
            for record in records:
                self._records[self._key(record.scope, record.space, record.record_id)] = record

    async def get(
        self, scope: TenantScope, space: EmbeddingSpace, record_ids: Sequence[str]
    ) -> Sequence[VectorRecord]:
        """Return the scoped projections present for the requested IDs, in request order."""
        async with self._lock:
            found = (self._records.get(self._key(scope, space, rid)) for rid in record_ids)
            return tuple(record for record in found if record is not None)

    async def search(
        self,
        scope: TenantScope,
        space: EmbeddingSpace,
        vector: Sequence[float],
        *,
        limit: int,
        metadata: JsonObject | None = None,
    ) -> Sequence[VectorMatch]:
        """Rank scoped projections by cosine distance to ``vector``.

        Raises:
            ValueError: If ``vector`` has the wrong dimensions or ``limit`` < 1.
        """
        validate_query(space, vector, limit)
        prefix = (scope.tenant_id, scope.namespace, space.space_id)
        async with self._lock:
            candidates = [
                record
                for key, record in self._records.items()
                if key[:3] == prefix and metadata_matches(record.metadata, metadata)
            ]
        matches = [
            VectorMatch(record=record, distance=cosine_distance(vector, record.vector))
            for record in candidates
        ]
        matches.sort(key=lambda match: (match.distance, match.record.record_id))
        return tuple(matches[:limit])

    async def delete(
        self, scope: TenantScope, space: EmbeddingSpace, record_ids: Sequence[str]
    ) -> None:
        """Idempotently remove the scoped projections with these IDs."""
        async with self._lock:
            for record_id in record_ids:
                self._records.pop(self._key(scope, space, record_id), None)

    async def health(self) -> None:
        """Report healthy; process memory is always available."""


__all__ = ["InMemoryVectorStore"]
