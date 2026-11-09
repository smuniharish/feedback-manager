"""LanceDB vector storage, installed with ``feedback-manager[lancedb]``.

Each embedding space is stored in its own LanceDB table, named from a hash of
the space ID, so vectors from incompatible spaces are never compared. Every
read, search, and delete is restricted with a SQL predicate on ``tenant_id``,
``namespace``, and ``space_id`` that LanceDB applies before ranking. String
literals in predicates are always escaped by `sql_string`; no caller data is
interpolated any other way.

Vectors are stored as ``float32``, so values read back are rounded to single
precision. Cosine distance is undefined for zero-length vectors; LanceDB omits
such rows from search results, and a zero query vector matches nothing.
"""

from __future__ import annotations

try:
    import lancedb
    import pyarrow as pa
    from lancedb.index import IvfPq
except ImportError as exc:  # pragma: no cover - exercised in a subprocess test
    raise ImportError(
        "feedback_manager.storage.vector.lancedb needs LanceDB; "
        'install it with: pip install "feedback-manager[lancedb]"'
    ) from exc

import asyncio
import hashlib
import json
import re
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from feedback_manager.learning.errors import LearningValidationError
from feedback_manager.storage.vector.base import (
    VectorCapabilities,
    VectorMatch,
    VectorRecord,
    VectorStore,
)
from feedback_manager.storage.vector.memory import metadata_matches, validate_query

if TYPE_CHECKING:
    from collections.abc import Awaitable, Iterable, Mapping, Sequence

    from lancedb.db import AsyncConnection
    from lancedb.table import AsyncTable

    from feedback_manager.core._types import JsonObject
    from feedback_manager.embeddings.models import EmbeddingSpace
    from feedback_manager.learning.models import TenantScope

TABLE_PREFIX = "fm_vectors_v1_"
"""Prefix of the table that holds each embedding space."""

VECTOR_COLUMN = "vector"
KEY_COLUMN = "key"

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_ID_BATCH = 256
_OVERFETCH = 4
_SCOPE_METADATA = ("visibility", "agent_id", "agent_type", "artifact_type")


def sql_string(value: str) -> str:
    """Return ``value`` as a safely quoted SQL string literal.

    Raises:
        ValueError: If ``value`` contains a NUL character.
    """
    if "\x00" in value:
        raise ValueError("SQL string literals must not contain NUL characters")
    return "'" + value.replace("'", "''") + "'"


def sql_identifier(name: str) -> str:
    """Return ``name`` after checking it is a plain SQL identifier.

    Raises:
        ValueError: If ``name`` is not a letter or underscore followed by up to
            127 letters, digits, or underscores.
    """
    if _IDENTIFIER.fullmatch(name) is None:
        raise ValueError(f"invalid SQL identifier: {name!r}")
    return name


def table_name(space: EmbeddingSpace) -> str:
    """Return the bounded, sanitized table name for an embedding space."""
    digest = hashlib.sha256(space.space_id.encode("utf-8")).hexdigest()[:16]
    return sql_identifier(f"{TABLE_PREFIX}{digest}")


def table_schema(dimensions: int) -> pa.Schema:
    """Return the Arrow schema of a table holding ``dimensions``-wide vectors."""
    return pa.schema(
        [
            pa.field(KEY_COLUMN, pa.string(), nullable=False),
            pa.field("record_id", pa.string(), nullable=False),
            pa.field("tenant_id", pa.string(), nullable=False),
            pa.field("namespace", pa.string(), nullable=False),
            pa.field("artifact_id", pa.string(), nullable=False),
            pa.field("artifact_version", pa.int64(), nullable=False),
            pa.field("space_id", pa.string(), nullable=False),
            pa.field(VECTOR_COLUMN, pa.list_(pa.float32(), dimensions), nullable=False),
            pa.field("metadata", pa.string(), nullable=False),
            *(pa.field(name, pa.string()) for name in _SCOPE_METADATA),
        ]
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _record_key(scope: TenantScope, record_id: str) -> str:
    return _canonical_json([scope.tenant_id, scope.namespace, record_id])


def _scope_predicate(scope: TenantScope, space: EmbeddingSpace) -> str:
    return " AND ".join(
        f"{sql_identifier(column)} = {sql_string(value)}"
        for column, value in (
            ("tenant_id", scope.tenant_id),
            ("namespace", scope.namespace),
            ("space_id", space.space_id),
        )
    )


def _ids_predicates(
    scope: TenantScope, space: EmbeddingSpace, record_ids: Sequence[str]
) -> Iterable[str]:
    unique = list(dict.fromkeys(record_ids))
    scoped = _scope_predicate(scope, space)
    for start in range(0, len(unique), _ID_BATCH):
        literals = ", ".join(sql_string(rid) for rid in unique[start : start + _ID_BATCH])
        yield f"{scoped} AND {sql_identifier('record_id')} IN ({literals})"


def _to_row(record: VectorRecord) -> dict[str, Any]:
    return {
        KEY_COLUMN: _record_key(record.scope, record.record_id),
        "record_id": record.record_id,
        "tenant_id": record.scope.tenant_id,
        "namespace": record.scope.namespace,
        "artifact_id": str(record.artifact_id),
        "artifact_version": record.artifact_version,
        "space_id": record.space.space_id,
        VECTOR_COLUMN: list(record.vector),
        "metadata": _canonical_json(record.metadata),
        **{
            name: value if isinstance(value := record.metadata.get(name), str) else None
            for name in _SCOPE_METADATA
        },
    }


def _distance(row: dict[str, Any]) -> float:
    return max(0.0, float(row["_distance"]))


def _to_record(row: dict[str, Any], scope: TenantScope, space: EmbeddingSpace) -> VectorRecord:
    return VectorRecord(
        record_id=row["record_id"],
        scope=scope,
        artifact_id=UUID(row["artifact_id"]),
        artifact_version=row["artifact_version"],
        space=space,
        vector=tuple(row[VECTOR_COLUMN]),
        metadata=json.loads(row["metadata"]),
    )


class LanceDBVectorStore(VectorStore):
    """Store vector projections in LanceDB, one table per embedding space.

    Upserts use ``merge_insert`` on a ``key`` column that combines tenant,
    namespace, and record ID, so repeated writes replace rather than duplicate.
    Search ranks by LanceDB cosine distance with scope prefiltering and exact
    re-ranking of candidates. Metadata filters are exact-match on top-level
    keys and are applied in Python: the store over-fetches scoped candidates
    and doubles the fetch size until enough matches are found or the scoped
    candidates are exhausted, so results equal a brute-force filtered search
    when no vector index is present.

    Example:
        ```python
        store = await LanceDBVectorStore.open("./vectors")
        await store.upsert(record)
        matches = await store.search(scope, space, query, limit=5)
        ```
    """

    def __init__(
        self,
        uri: str,
        *,
        connection: AsyncConnection | None = None,
        connect_options: Mapping[str, Any] | None = None,
        max_search_candidates: int = 10000,
    ) -> None:
        """Create a store that connects lazily to ``uri``.

        Args:
            uri: A LanceDB URI, such as a local directory, an ``s3://`` path,
                or a ``db://`` LanceDB Cloud database.
            connection: An already opened async connection to use instead of
                connecting to ``uri``.
            connect_options: Extra ``lancedb.connect_async`` keyword arguments,
                such as ``api_key``, ``region``, ``storage_options``,
                ``read_consistency_interval``, or ``client_config``.
            max_search_candidates: Maximum rows fetched for metadata and tie resolution.

        Raises:
            ValueError: If ``max_search_candidates`` is not positive, or
                ``connect_options`` is combined with ``connection``.
        """
        if max_search_candidates < 1:
            raise ValueError("max_search_candidates must be positive")
        if connection is not None and connect_options:
            raise ValueError("connect_options apply only when the store opens the connection")
        self._max_search_candidates = max_search_candidates
        self._uri = uri
        self._connection = connection
        self._connect_options = dict(connect_options or {})
        self._tables: dict[str, AsyncTable] = {}
        self._lock = asyncio.Lock()

    @classmethod
    async def open(
        cls,
        uri: str,
        *,
        connection: AsyncConnection | None = None,
        connect_options: Mapping[str, Any] | None = None,
        max_search_candidates: int = 10000,
    ) -> LanceDBVectorStore:
        """Create a store with the same arguments as the constructor and verify it is reachable."""
        store = cls(
            uri,
            connection=connection,
            connect_options=connect_options,
            max_search_candidates=max_search_candidates,
        )
        await store.health()
        return store

    @property
    def uri(self) -> str:
        """The LanceDB URI this store was created for."""
        return self._uri

    @property
    def capabilities(self) -> VectorCapabilities:
        """Describe optional operations supported by this adapter."""
        return VectorCapabilities(metadata_filtering=True, index_management=True)

    async def _connect_locked(self) -> AsyncConnection:
        if self._connection is None:
            self._connection = await lancedb.connect_async(self._uri, **self._connect_options)
        return self._connection

    async def _table(self, space: EmbeddingSpace, *, create: bool) -> AsyncTable | None:
        name = table_name(space)
        async with self._lock:
            table = self._tables.get(name)
            if table is not None:
                return table
            connection = await self._connect_locked()
            if create:
                table = await connection.create_table(
                    name, schema=table_schema(space.dimensions), exist_ok=True
                )
            else:
                try:
                    table = await connection.open_table(name)
                except ValueError as exc:
                    if str(exc) != f"Table '{name}' was not found":
                        raise
                    return None
            self._tables[name] = table
            return table

    async def upsert_many(self, records: Sequence[VectorRecord]) -> None:
        """Idempotently insert or replace a batch of projections.

        Raises:
            ValueError: If a record's vector does not match its space dimensions.
        """
        batches: dict[str, tuple[EmbeddingSpace, dict[str, dict[str, Any]]]] = {}
        for record in records:
            if len(record.vector) != record.space.dimensions:
                raise ValueError("vector dimensions do not match the embedding space")
            row = _to_row(record)
            batches.setdefault(record.space.space_id, (record.space, {}))[1][row[KEY_COLUMN]] = row
        for space, rows in batches.values():
            table = cast("AsyncTable", await self._table(space, create=True))
            data = pa.Table.from_pylist(list(rows.values()), schema=table_schema(space.dimensions))
            builder = (
                table.merge_insert(KEY_COLUMN)
                .when_matched_update_all()
                .when_not_matched_insert_all()
            )
            await cast("Awaitable[object]", builder.execute(data))

    async def get(
        self, scope: TenantScope, space: EmbeddingSpace, record_ids: Sequence[str]
    ) -> Sequence[VectorRecord]:
        """Return the scoped projections present for the requested IDs, in request order."""
        table = await self._table(space, create=False)
        if table is None:
            return ()
        found: dict[str, VectorRecord] = {}
        for predicate in _ids_predicates(scope, space, record_ids):
            for row in await table.query().where(predicate).to_list():
                found[row["record_id"]] = _to_record(row, scope, space)
        return tuple(found[rid] for rid in record_ids if rid in found)

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
        if limit > self._max_search_candidates:
            raise ValueError("limit exceeds max_search_candidates")
        table = await self._table(space, create=False)
        if table is None:
            return ()
        predicate = _scope_predicate(scope, space)
        if metadata:
            for key in _SCOPE_METADATA:
                value = metadata.get(key)
                if isinstance(value, str):
                    predicate += f" AND {sql_identifier(key)} = {sql_string(value)}"
        query_vector = [float(value) for value in vector]
        fetch = min(limit * _OVERFETCH if metadata else limit, self._max_search_candidates)
        while True:
            rows = (
                await table.vector_search(query_vector)
                .distance_type("cosine")
                .where(predicate)
                .refine_factor(1)
                .limit(fetch)
                .to_list()
            )
            matches = sorted(
                (
                    VectorMatch(record=_to_record(row, scope, space), distance=_distance(row))
                    for row in rows
                    if metadata_matches(json.loads(row["metadata"]), metadata)
                ),
                key=lambda match: (match.distance, match.record.record_id),
            )
            # Stop once scoped rows are exhausted, or once every unfetched row is
            # strictly farther than the last kept match, so ties at the cutoff
            # are still ordered by record ID.
            if len(rows) < fetch or (
                len(matches) >= limit and _distance(rows[-1]) > matches[limit - 1].distance
            ):
                return tuple(matches[:limit])
            if fetch == self._max_search_candidates:
                raise LearningValidationError("vector search exceeded max_search_candidates")
            fetch = min(fetch * 2, self._max_search_candidates)

    async def delete(
        self, scope: TenantScope, space: EmbeddingSpace, record_ids: Sequence[str]
    ) -> None:
        """Idempotently remove the scoped projections with these IDs."""
        table = await self._table(space, create=False)
        if table is None:
            return
        for predicate in _ids_predicates(scope, space, record_ids):
            await table.delete(predicate)

    async def create_index(self, scope: TenantScope, space: EmbeddingSpace) -> None:
        """Create or replace an IVF-PQ cosine index on the space's vector column.

        The index is physical and covers every tenant in the embedding space;
        ``scope`` only names the caller and isolation is still enforced by the
        search predicate. LanceDB trains the index on the rows present when it
        is built, so call this after loading representative data and again
        after large changes. Once indexed, search is approximate: candidates
        are re-ranked with exact distances, but rows in unprobed partitions can
        be missed.
        """
        del scope
        table = cast("AsyncTable", await self._table(space, create=True))
        await table.create_index(VECTOR_COLUMN, config=IvfPq(distance_type="cosine"), replace=True)

    async def health(self) -> None:
        """Connect to the database and list a table to verify it is reachable."""
        async with self._lock:
            connection = await self._connect_locked()
        await connection.list_tables(limit=1)


__all__ = [
    "TABLE_PREFIX",
    "LanceDBVectorStore",
    "sql_identifier",
    "sql_string",
    "table_name",
    "table_schema",
]
