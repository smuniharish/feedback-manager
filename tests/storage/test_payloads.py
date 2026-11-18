import asyncio
import hashlib
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from feedback_manager.learning import LearningValidationError
from feedback_manager.storage import (
    InMemoryPayloadStore,
    PayloadCodec,
    PayloadConflictError,
    PayloadIntegrityError,
    PayloadMetadata,
    PayloadScope,
    PayloadStoreABC,
    PayloadStoreError,
    SQLitePayloadStore,
)

SCOPE = PayloadScope(tenant_id="tenant", namespace="app", agent_id="agent")


class ReversingCodec(PayloadCodec):
    async def encode(self, scope: PayloadScope, data: bytes) -> bytes:
        await asyncio.sleep(0)
        return data[::-1]

    async def decode(self, scope: PayloadScope, data: bytes) -> bytes:
        return data[::-1]


class BadCodec(PayloadCodec):
    def __init__(self, *, encode_result: object, decode_result: object) -> None:
        self.encode_result = encode_result
        self.decode_result = decode_result

    async def encode(self, scope: PayloadScope, data: bytes) -> bytes:
        return self.encode_result  # type: ignore[return-value]

    async def decode(self, scope: PayloadScope, data: bytes) -> bytes:
        return self.decode_result  # type: ignore[return-value]


class BarrierCodec(PayloadCodec):
    def __init__(self) -> None:
        self.count = 0
        self.ready = asyncio.Event()
        self.proceed = asyncio.Event()

    async def encode(self, scope: PayloadScope, data: bytes) -> bytes:
        self.count += 1
        if self.count == 2:
            self.ready.set()
        await self.proceed.wait()
        return data

    async def decode(self, scope: PayloadScope, data: bytes) -> bytes:
        return data


def test_payload_scope_requires_explicit_namespace_and_private_agent() -> None:
    with pytest.raises(ValidationError):
        PayloadScope.model_validate({"tenant_id": "tenant", "agent_id": "agent"})
    with pytest.raises(ValidationError):
        PayloadScope(tenant_id="tenant", namespace="app")


async def test_codec_round_trip_and_logical_digest() -> None:
    store = InMemoryPayloadStore(codec=ReversingCodec())
    assert isinstance(store, PayloadStoreABC)
    record = await store.write(
        SCOPE, b"original", content_type="application/octet-stream", serialization_version="v1"
    )
    assert record.digest == hashlib.sha256(b"original").hexdigest()
    assert record.size == len(b"original")
    assert await store.read(SCOPE, record.payload_id) == b"original"


async def test_digest_work_does_not_block_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    entered, release = threading.Event(), threading.Event()

    def slow_digest(data: bytes) -> str:
        entered.set()
        if not release.wait(timeout=5):
            raise TimeoutError("digest did not finish")
        return hashlib.sha256(data).hexdigest()

    monkeypatch.setattr("feedback_manager.storage.payload._digest", slow_digest)
    store = InMemoryPayloadStore()
    task = asyncio.create_task(
        store.write(SCOPE, b"data", content_type="text/plain", serialization_version="1")
    )
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait), timeout=2)
    finally:
        release.set()
        await task


@pytest.mark.parametrize(("second", "conflicts"), [(b"first", 0), (b"second", 1)])
async def test_concurrent_writes_do_not_replace_the_winner(second: bytes, conflicts: int) -> None:
    codec = BarrierCodec()
    store = InMemoryPayloadStore(codec=codec)
    payload_id = uuid4()
    tasks = [
        asyncio.create_task(
            store.write(
                SCOPE,
                data,
                content_type="text/plain",
                serialization_version="1",
                payload_id=payload_id,
            )
        )
        for data in (b"first", second)
    ]
    try:
        await asyncio.wait_for(codec.ready.wait(), timeout=2)
    finally:
        codec.proceed.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
    assert sum(isinstance(result, PayloadConflictError) for result in results) == conflicts
    assert sum(isinstance(result, PayloadMetadata) for result in results) == 2 - conflicts
    assert await store.read(SCOPE, payload_id) in (b"first", b"second")


async def test_bad_codec_results_and_corruption_are_explicit_errors() -> None:
    store = InMemoryPayloadStore(codec=BadCodec(encode_result="not bytes", decode_result=b""))
    with pytest.raises(PayloadIntegrityError, match="non-bytes"):
        await store.write(SCOPE, b"data", content_type="text/plain", serialization_version="1")

    store = InMemoryPayloadStore(codec=BadCodec(encode_result=b"stored", decode_result="not bytes"))
    record = await store.write(SCOPE, b"data", content_type="text/plain", serialization_version="1")
    with pytest.raises(PayloadIntegrityError, match="non-bytes"):
        await store.read(SCOPE, record.payload_id)

    for corrupted in (b"other", b"evil"):
        store = InMemoryPayloadStore(
            codec=BadCodec(encode_result=b"stored", decode_result=corrupted)
        )
        record = await store.write(
            SCOPE, b"data", content_type="text/plain", serialization_version="1"
        )
        with pytest.raises(PayloadIntegrityError, match="integrity"):
            await store.read(SCOPE, record.payload_id)


async def test_invalid_store_inputs_are_rejected() -> None:
    with pytest.raises(TypeError, match="codec"):
        InMemoryPayloadStore(codec=object())  # type: ignore[arg-type]
    store = InMemoryPayloadStore()
    with pytest.raises(TypeError, match="scope"):
        await store.read("tenant", uuid4())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="UUID"):
        await store.delete(SCOPE, "bad")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="bytes"):
        await store.write(SCOPE, "data", content_type="text/plain", serialization_version="1")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="UUID"):
        await store.write(
            SCOPE,
            b"data",
            content_type="text/plain",
            serialization_version="1",
            payload_id="bad",  # type: ignore[arg-type]
        )
    forged = SCOPE.model_copy(update={"agent_id": None})
    with pytest.raises(ValidationError):
        await store.write(forged, b"data", content_type="text/plain", serialization_version="1")


async def test_sqlite_payloads_survive_restart_and_store_encoded_bytes(tmp_path: Path) -> None:
    path = tmp_path / "payloads.db"
    store = await SQLitePayloadStore.open(path, codec=ReversingCodec())
    record = await store.write(
        SCOPE, b"durable", content_type="text/plain", serialization_version="1"
    )
    await store.close()
    await store.close()
    with pytest.raises(PayloadStoreError, match="closed"):
        await store.health()

    with closing(sqlite3.connect(path)) as connection, connection:
        (stored,) = connection.execute("SELECT data FROM payloads").fetchone()
    assert stored == b"elbarud"

    reopened = await SQLitePayloadStore.open(path, codec=ReversingCodec())
    try:
        assert await reopened.read(SCOPE, record.payload_id) == b"durable"
        assert await reopened.metadata(SCOPE, record.payload_id) == record
    finally:
        await reopened.close()


@pytest.mark.parametrize(("second", "conflicts"), [(b"first", 0), (b"second", 1)])
async def test_sqlite_concurrent_writes_keep_the_first_committed_payload(
    tmp_path: Path, second: bytes, conflicts: int
) -> None:
    codec = BarrierCodec()
    store = await SQLitePayloadStore.open(tmp_path / "payloads.db", codec=codec)
    payload_id = uuid4()
    tasks = [
        asyncio.create_task(
            store.write(
                SCOPE,
                data,
                content_type="text/plain",
                serialization_version="1",
                payload_id=payload_id,
            )
        )
        for data in (b"first", second)
    ]
    try:
        await asyncio.wait_for(codec.ready.wait(), timeout=2)
    finally:
        codec.proceed.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
    try:
        assert sum(isinstance(result, PayloadConflictError) for result in results) == conflicts
        assert await store.read(SCOPE, payload_id) in (b"first", b"second")
    finally:
        await store.close()


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        ("UPDATE payloads SET metadata='not json'", "metadata is invalid"),
        ("UPDATE payloads SET payload_id='00000000-0000-0000-0000-000000000000'", "key"),
        ("UPDATE payloads SET data='text'", "key"),
    ],
)
async def test_sqlite_tampered_rows_fail_integrity_checks(
    tmp_path: Path, statement: str, message: str
) -> None:
    path = tmp_path / "payloads.db"
    store = await SQLitePayloadStore.open(path)
    record = await store.write(SCOPE, b"data", content_type="text/plain", serialization_version="1")
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(statement)
    payload_id = record.payload_id if "payload_id" not in statement else UUID(int=0)
    try:
        with pytest.raises(PayloadIntegrityError, match=message):
            await store.read(SCOPE, payload_id)
    finally:
        await store.close()


async def test_sqlite_store_rejects_invalid_configuration(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="codec"):
        SQLitePayloadStore(tmp_path / "x.db", codec=object())  # type: ignore[arg-type]
    with pytest.raises(LearningValidationError, match="local database file"):
        SQLitePayloadStore(":memory:")
