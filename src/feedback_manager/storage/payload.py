"""Scoped storage for original payload bytes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from feedback_manager.core._types import Identifier
from feedback_manager.learning.errors import LearningError
from feedback_manager.learning.models import ArtifactScope
from feedback_manager.storage._sqlite import SQLiteHandle, open_with_cleanup

if TYPE_CHECKING:
    from pathlib import Path


class PayloadScope(ArtifactScope):
    """Host-authenticated payload scope with an explicitly supplied namespace."""

    model_config = ConfigDict(revalidate_instances="always")

    namespace: Identifier = Field(...)


class PayloadMetadata(BaseModel):
    """The logical content and scope of one payload, with a SHA-256 digest."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    payload_id: UUID
    scope: PayloadScope
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    content_type: Identifier
    serialization_version: Identifier


class PayloadStoreError(LearningError):
    """Base class for payload-storage failures."""


class PayloadNotFoundError(PayloadStoreError, LookupError):
    """A payload does not exist in the requested scope."""


class PayloadConflictError(PayloadStoreError):
    """An existing payload ID was reused for different content or metadata."""


class PayloadIntegrityError(PayloadStoreError):
    """Stored or decoded payload bytes do not match their metadata."""


class PayloadCodec(ABC):
    """Encode and decode payloads using application-managed components.

    Implementations may provide encryption. The store does not close an
    injected codec, and does not claim that a codec encrypts its input.
    """

    @abstractmethod
    async def encode(self, scope: PayloadScope, data: bytes) -> bytes:
        """Return the bytes to store."""

    @abstractmethod
    async def decode(self, scope: PayloadScope, data: bytes) -> bytes:
        """Return the original logical payload bytes."""


class PayloadStoreABC(ABC):
    """Persist payload bytes in an authenticated scope.

    The host must authorize the requested scope. The digest and size describe
    the logical bytes before any encoding. Reusing an ID with identical
    content and metadata is idempotent; conflicting reuse raises an error.
    """

    durable: bool = False

    @abstractmethod
    async def write(
        self,
        scope: PayloadScope,
        data: bytes,
        *,
        content_type: str,
        serialization_version: str,
        payload_id: UUID | None = None,
    ) -> PayloadMetadata:
        """Write bytes and return their metadata."""

    @abstractmethod
    async def read(self, scope: PayloadScope, payload_id: UUID) -> bytes:
        """Read and verify bytes, or raise `PayloadNotFoundError`."""

    @abstractmethod
    async def metadata(self, scope: PayloadScope, payload_id: UUID) -> PayloadMetadata:
        """Return metadata, or raise `PayloadNotFoundError`."""

    @abstractmethod
    async def delete(self, scope: PayloadScope, payload_id: UUID) -> bool:
        """Delete a payload and return whether it existed in the scope."""

    @abstractmethod
    async def health(self) -> None:
        """Check store availability."""


class InMemoryPayloadStore(PayloadStoreABC):
    """Keep process-local payloads, optionally encoded by a caller-owned codec."""

    def __init__(self, *, codec: PayloadCodec | None = None) -> None:
        if codec is not None and not isinstance(codec, PayloadCodec):
            raise TypeError("codec must be a PayloadCodec")
        self._codec = codec
        self._lock = threading.Lock()
        self._records: dict[tuple[PayloadScope, UUID], tuple[PayloadMetadata, bytes]] = {}

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
        scope = record.scope
        key = (scope, record.payload_id)
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                return _identical(existing[0], record)
        encoded = await _encode(self._codec, scope, data)
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                return _identical(existing[0], record)
            self._records[key] = (record, encoded)
        return record

    async def read(self, scope: PayloadScope, payload_id: UUID) -> bytes:
        """Read logical bytes and verify their size and digest."""
        record, encoded = self._get(scope, payload_id)
        return await _decode(self._codec, record, encoded)

    async def metadata(self, scope: PayloadScope, payload_id: UUID) -> PayloadMetadata:
        """Return the scoped payload metadata."""
        return self._get(scope, payload_id)[0]

    async def delete(self, scope: PayloadScope, payload_id: UUID) -> bool:
        """Delete the payload only from the supplied scope."""
        scope = _check_key(scope, payload_id)
        with self._lock:
            return self._records.pop((scope, payload_id), None) is not None

    async def health(self) -> None:
        """The in-memory store has no external persistence dependency."""

    def _get(self, scope: PayloadScope, payload_id: UUID) -> tuple[PayloadMetadata, bytes]:
        scope = _check_key(scope, payload_id)
        with self._lock:
            record = self._records.get((scope, payload_id))
        if record is None:
            raise PayloadNotFoundError("payload not found in the requested scope")
        return record


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS payloads (
    scope TEXT NOT NULL,
    payload_id TEXT NOT NULL,
    metadata TEXT NOT NULL,
    data BLOB NOT NULL,
    PRIMARY KEY (scope, payload_id)
);
"""


class SQLitePayloadStore(PayloadStoreABC):
    """Durable single-host payload archive in one local SQLite file.

    Use `open` and `close` to avoid blocking the event loop. An injected codec
    is caller-owned and runs outside the database lock.
    """

    durable = True

    def __init__(self, path: str | Path, *, codec: PayloadCodec | None = None) -> None:
        if codec is not None and not isinstance(codec, PayloadCodec):
            raise TypeError("codec must be a PayloadCodec")
        self._codec = codec
        self._handle = SQLiteHandle(
            path, _SQLITE_SCHEMA, name="payload", error_type=PayloadStoreError
        )
        self._connection = self._handle.connection

    @classmethod
    async def open(
        cls, path: str | Path, *, codec: PayloadCodec | None = None
    ) -> SQLitePayloadStore:
        """Open a local archive without blocking the event loop."""
        return await open_with_cleanup(lambda: cls(path, codec=codec), lambda store: store.close())

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
        existing = await self._handle.run(lambda: self._fetch(key))
        if existing is not None:
            return _identical(existing[0], record)
        encoded = await _encode(self._codec, record.scope, data)

        def insert() -> PayloadMetadata:
            self._connection.execute(
                "INSERT INTO payloads (scope,payload_id,metadata,data) VALUES (?,?,?,?) "
                "ON CONFLICT (scope,payload_id) DO NOTHING",
                (*key, record.model_dump_json(), encoded),
            )
            stored = self._fetch(key)
            if stored is None:  # pragma: no cover - the row was just inserted or already existed
                raise PayloadStoreError("payload write was not persisted")
            return _identical(stored[0], record)

        return await self._handle.run(insert)

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

        def delete() -> bool:
            cursor = self._connection.execute(
                "DELETE FROM payloads WHERE scope=? AND payload_id=?", key
            )
            return cursor.rowcount > 0

        return await self._handle.run(delete)

    async def health(self) -> None:
        """Check that the connection is usable."""
        await self._handle.run(lambda: self._connection.execute("SELECT 1").fetchone())

    async def close(self) -> None:
        """Wait for an ongoing operation and close the connection idempotently."""
        await self._handle.close()

    async def _get(self, scope: PayloadScope, payload_id: UUID) -> tuple[PayloadMetadata, bytes]:
        key = _row_key(_check_key(scope, payload_id), payload_id)
        record = await self._handle.run(lambda: self._fetch(key))
        if record is None:
            raise PayloadNotFoundError("payload not found in the requested scope")
        return record

    def _fetch(self, key: tuple[str, str]) -> tuple[PayloadMetadata, bytes] | None:
        row = self._connection.execute(
            "SELECT metadata,data FROM payloads WHERE scope=? AND payload_id=?", key
        ).fetchone()
        return None if row is None else _stored(key, row[0], row[1])


def payload_scope_key(scope: PayloadScope) -> str:
    """Return the canonical storage key for a validated payload scope."""
    return json.dumps(scope.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _row_key(scope: PayloadScope, payload_id: UUID) -> tuple[str, str]:
    return payload_scope_key(scope), str(payload_id)


def _stored(key: tuple[str, str], metadata: str, data: object) -> tuple[PayloadMetadata, bytes]:
    try:
        record = PayloadMetadata.model_validate_json(metadata)
    except ValueError as exc:
        raise PayloadIntegrityError("stored payload metadata is invalid") from exc
    if _row_key(record.scope, record.payload_id) != key or not isinstance(
        data, (bytes, bytearray, memoryview)
    ):
        raise PayloadIntegrityError("stored payload does not match its key")
    return record, bytes(data)


async def _prepare(
    scope: PayloadScope,
    data: bytes,
    *,
    content_type: str,
    serialization_version: str,
    payload_id: UUID | None,
) -> PayloadMetadata:
    scope = _check_scope(scope)
    if not isinstance(data, bytes):
        raise TypeError("payload data must be bytes")
    if payload_id is not None and not isinstance(payload_id, UUID):
        raise TypeError("payload_id must be a UUID")
    return PayloadMetadata(
        payload_id=payload_id if payload_id is not None else uuid4(),
        scope=scope,
        digest=await asyncio.to_thread(_digest, data),
        size=len(data),
        content_type=content_type,
        serialization_version=serialization_version,
    )


async def _encode(codec: PayloadCodec | None, scope: PayloadScope, data: bytes) -> bytes:
    encoded = data if codec is None else await codec.encode(scope, data)
    if not isinstance(encoded, bytes):
        raise PayloadIntegrityError("payload codec returned non-bytes")
    return encoded


async def _decode(codec: PayloadCodec | None, record: PayloadMetadata, encoded: bytes) -> bytes:
    data = encoded if codec is None else await codec.decode(record.scope, encoded)
    if not isinstance(data, bytes):
        raise PayloadIntegrityError("payload codec returned non-bytes")
    if len(data) != record.size or await asyncio.to_thread(_digest, data) != record.digest:
        raise PayloadIntegrityError("payload content failed integrity validation")
    return data


def _check_scope(scope: PayloadScope) -> PayloadScope:
    if not isinstance(scope, PayloadScope):
        raise TypeError("scope must be a PayloadScope")
    return PayloadScope.model_validate(scope.model_dump())


def _check_key(scope: PayloadScope, payload_id: UUID) -> PayloadScope:
    scope = _check_scope(scope)
    if not isinstance(payload_id, UUID):
        raise TypeError("payload_id must be a UUID")
    return scope


def _identical(existing: PayloadMetadata, proposed: PayloadMetadata) -> PayloadMetadata:
    if existing != proposed:
        raise PayloadConflictError("payload ID already exists with different content or metadata")
    return existing


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


__all__ = [
    "InMemoryPayloadStore",
    "PayloadCodec",
    "PayloadConflictError",
    "PayloadIntegrityError",
    "PayloadMetadata",
    "PayloadNotFoundError",
    "PayloadScope",
    "PayloadStoreABC",
    "PayloadStoreError",
    "SQLitePayloadStore",
    "payload_scope_key",
]
