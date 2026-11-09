"""Contract tests shared by every `VectorStore` adapter, plus adapter specifics."""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from feedback_manager.embeddings import EmbeddingSpace
from feedback_manager.learning import TenantScope, UnsupportedCapabilityError
from feedback_manager.storage.vector import InMemoryVectorStore, VectorRecord, VectorStore

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from feedback_manager.core._types import JsonObject
    from feedback_manager.storage.vector import VectorMatch

SPACE = EmbeddingSpace(provider="example", model="model", revision="1", dimensions=2)
OTHER_SPACE = EmbeddingSpace(provider="example", model="other", revision="1", dimensions=2)
WIDE_SPACE = EmbeddingSpace(provider="example", model="wide", revision="1", dimensions=3)
SCOPE = TenantScope(tenant_id="tenant")
OTHER_TENANT = TenantScope(tenant_id="other")
OTHER_NAMESPACE = TenantScope(tenant_id="tenant", namespace="private")
HOSTILE = "a' OR '1'='1"


def make_record(
    record_id: str,
    vector: tuple[float, ...] = (1.0, 0.0),
    *,
    scope: TenantScope = SCOPE,
    space: EmbeddingSpace = SPACE,
    metadata: JsonObject | None = None,
    version: int = 1,
) -> VectorRecord:
    return VectorRecord(
        record_id=record_id,
        scope=scope,
        artifact_id=uuid4(),
        artifact_version=version,
        space=space,
        vector=vector,
        metadata=metadata or {},
    )


def ids(matches: Iterable[VectorMatch]) -> list[str]:
    return [match.record.record_id for match in matches]


class VectorStoreContract:
    """Behavior every adapter must provide; subclasses supply a `store` fixture."""

    async def test_upsert_is_idempotent(self, store: VectorStore) -> None:
        first = make_record("one", metadata={"kind": "old"})
        second = first.model_copy(update={"metadata": {"kind": "new"}, "artifact_version": 2})
        await store.upsert(first)
        await store.upsert(first)
        await store.upsert(second)
        assert await store.get(SCOPE, SPACE, ["one"]) == (second,)
        assert ids(await store.search(SCOPE, SPACE, (1.0, 0.0), limit=10)) == ["one"]

    async def test_last_duplicate_in_a_batch_wins(self, store: VectorStore) -> None:
        first = make_record("one", metadata={"n": 1})
        second = make_record("one", metadata={"n": 2})
        await store.upsert_many([first, second])
        assert await store.get(SCOPE, SPACE, ["one"]) == (second,)

    async def test_get_returns_present_records_in_request_order(self, store: VectorStore) -> None:
        records = [make_record(name) for name in ("a", "b", "c")]
        await store.upsert_many(records)
        found = await store.get(SCOPE, SPACE, ["c", "missing", "a"])
        assert found == (records[2], records[0])
        assert await store.get(SCOPE, SPACE, []) == ()

    async def test_reads_before_any_write_are_empty(self, store: VectorStore) -> None:
        assert await store.get(SCOPE, SPACE, ["one"]) == ()
        assert await store.search(SCOPE, SPACE, (1.0, 0.0), limit=3) == ()
        await store.delete(SCOPE, SPACE, ["one"])

    async def test_tenant_and_namespace_isolation(self, store: VectorStore) -> None:
        mine = make_record("shared", metadata={"owner": "mine"})
        theirs = make_record("shared", scope=OTHER_TENANT, metadata={"owner": "tenant"})
        private = make_record("shared", scope=OTHER_NAMESPACE, metadata={"owner": "namespace"})
        await store.upsert_many([mine, theirs, private])
        assert await store.get(SCOPE, SPACE, ["shared"]) == (mine,)
        assert await store.get(OTHER_TENANT, SPACE, ["shared"]) == (theirs,)
        assert await store.get(OTHER_NAMESPACE, SPACE, ["shared"]) == (private,)
        matches = await store.search(SCOPE, SPACE, (1.0, 0.0), limit=10)
        assert [match.record for match in matches] == [mine]
        await store.delete(SCOPE, SPACE, ["shared"])
        assert await store.get(SCOPE, SPACE, ["shared"]) == ()
        assert await store.get(OTHER_TENANT, SPACE, ["shared"]) == (theirs,)
        assert await store.get(OTHER_NAMESPACE, SPACE, ["shared"]) == (private,)

    async def test_embedding_space_isolation(self, store: VectorStore) -> None:
        base = make_record("same")
        other = make_record("same", space=OTHER_SPACE)
        wide = make_record("same", (1.0, 0.0, 0.0), space=WIDE_SPACE)
        await store.upsert_many([base, other, wide])
        assert await store.get(SCOPE, SPACE, ["same"]) == (base,)
        assert await store.get(SCOPE, OTHER_SPACE, ["same"]) == (other,)
        assert await store.get(SCOPE, WIDE_SPACE, ["same"]) == (wide,)
        assert [m.record for m in await store.search(SCOPE, SPACE, (1.0, 0.0), limit=5)] == [base]
        await store.delete(SCOPE, OTHER_SPACE, ["same"])
        assert await store.get(SCOPE, SPACE, ["same"]) == (base,)
        assert await store.get(SCOPE, OTHER_SPACE, ["same"]) == ()

    async def test_search_ranks_by_cosine_distance(self, store: VectorStore) -> None:
        await store.upsert_many(
            [
                make_record("opposite", (-1.0, 0.0)),
                make_record("orthogonal", (0.0, 2.0)),
                make_record("diagonal", (0.5, 0.5)),
                make_record("exact", (4.0, 0.0)),
            ]
        )
        matches = await store.search(SCOPE, SPACE, (1.0, 0.0), limit=10)
        assert ids(matches) == ["exact", "diagonal", "orthogonal", "opposite"]
        distances = [match.distance for match in matches]
        expected = [0.0, 1 - 2**-0.5, 1.0, 2.0]
        assert distances == pytest.approx(expected, abs=1e-6)

    async def test_ties_are_broken_by_record_id(self, store: VectorStore) -> None:
        await store.upsert_many([make_record(name) for name in ("c", "a", "b")])
        assert ids(await store.search(SCOPE, SPACE, (2.0, 0.0), limit=2)) == ["a", "b"]

    async def test_search_respects_limit(self, store: VectorStore) -> None:
        await store.upsert_many([make_record(f"r{i}", (1.0, i / 8)) for i in range(5)])
        matches = await store.search(SCOPE, SPACE, (1.0, 0.0), limit=3)
        assert ids(matches) == ["r0", "r1", "r2"]

    async def test_metadata_filter_matches_every_pair(self, store: VectorStore) -> None:
        await store.upsert_many(
            [
                make_record("close", (1.0, 0.0), metadata={"kind": "rule", "tool": "x"}),
                make_record("wrong", (1.0, 0.125), metadata={"kind": "rule", "tool": "y"}),
                make_record("missing", (1.0, 0.25), metadata={"kind": "rule"}),
                make_record("far", (0.0, 1.0), metadata={"kind": "rule", "tool": "x", "z": 1}),
            ]
        )
        matches = await store.search(
            SCOPE, SPACE, (1.0, 0.0), limit=5, metadata={"kind": "rule", "tool": "x"}
        )
        assert ids(matches) == ["close", "far"]
        assert await store.search(SCOPE, SPACE, (1.0, 0.0), limit=5, metadata={"x": None}) == ()
        unfiltered = await store.search(SCOPE, SPACE, (1.0, 0.0), limit=5, metadata={})
        assert len(unfiltered) == 4

    async def test_metadata_filter_finds_rare_matches_beyond_first_fetch(
        self, store: VectorStore
    ) -> None:
        records = [make_record(f"n{i}", (1.0, i / 16), metadata={"hit": False}) for i in range(9)]
        records.append(make_record("rare", (0.0, 1.0), metadata={"hit": True}))
        await store.upsert_many(records)
        matches = await store.search(SCOPE, SPACE, (1.0, 0.0), limit=1, metadata={"hit": True})
        assert ids(matches) == ["rare"]

    async def test_invalid_queries_are_rejected(self, store: VectorStore) -> None:
        await store.upsert(make_record("one"))
        with pytest.raises(ValueError, match="dimensions"):
            await store.search(SCOPE, SPACE, (1.0, 0.0, 0.0), limit=1)
        with pytest.raises(ValueError, match="limit"):
            await store.search(SCOPE, SPACE, (1.0, 0.0), limit=0)

    async def test_mismatched_record_dimensions_are_rejected(self, store: VectorStore) -> None:
        bad = make_record("bad").model_copy(update={"vector": (1.0,)})
        with pytest.raises(ValueError, match="dimensions"):
            await store.upsert(bad)
        assert await store.get(SCOPE, SPACE, ["bad"]) == ()

    async def test_delete_is_idempotent(self, store: VectorStore) -> None:
        await store.upsert_many([make_record("a"), make_record("b")])
        await store.delete(SCOPE, SPACE, ["a", "a", "missing"])
        await store.delete(SCOPE, SPACE, ["a"])
        await store.delete(SCOPE, SPACE, [])
        assert [r.record_id for r in await store.get(SCOPE, SPACE, ["a", "b"])] == ["b"]

    async def test_health(self, store: VectorStore) -> None:
        await store.health()

    async def test_hostile_identifiers_are_treated_as_data(self, store: VectorStore) -> None:
        hostile_scope = TenantScope(tenant_id=HOSTILE, namespace="ns'--")
        hostile = make_record(HOSTILE, scope=hostile_scope)
        slashed = make_record("back\\slash'", scope=hostile_scope)
        victim = make_record("victim")
        await store.upsert_many([hostile, slashed, victim])
        assert await store.get(hostile_scope, SPACE, [HOSTILE, "back\\slash'"]) == (
            hostile,
            slashed,
        )
        assert await store.get(SCOPE, SPACE, [HOSTILE, "victim"]) == (victim,)
        assert await store.get(TenantScope(tenant_id="a"), SPACE, [HOSTILE]) == ()
        found = await store.search(hostile_scope, SPACE, (1.0, 0.0), limit=10)
        assert ids(found) == [HOSTILE, "back\\slash'"]
        await store.delete(SCOPE, SPACE, [HOSTILE, "x' OR record_id != '"])
        await store.delete(hostile_scope, SPACE, [HOSTILE])
        assert await store.get(hostile_scope, SPACE, [HOSTILE, "back\\slash'"]) == (slashed,)
        assert await store.get(SCOPE, SPACE, ["victim"]) == (victim,)


class TestInMemoryVectorStore(VectorStoreContract):
    @pytest.fixture
    def store(self) -> VectorStore:
        return InMemoryVectorStore()

    def test_capabilities(self) -> None:
        capabilities = InMemoryVectorStore().capabilities
        assert capabilities.metadata_filtering
        assert not capabilities.index_management

    async def test_index_management_is_unsupported(self, store: VectorStore) -> None:
        with pytest.raises(UnsupportedCapabilityError):
            await store.create_index(SCOPE, SPACE)

    async def test_zero_vectors_have_unit_distance(self, store: VectorStore) -> None:
        await store.upsert_many([make_record("zero", (0.0, 0.0)), make_record("one")])
        matches = await store.search(SCOPE, SPACE, (1.0, 0.0), limit=5)
        assert [(m.record.record_id, m.distance) for m in matches] == [("one", 0.0), ("zero", 1.0)]
        matches = await store.search(SCOPE, SPACE, (0.0, 0.0), limit=5)
        assert [m.distance for m in matches] == [1.0, 1.0]


class TestLanceDBVectorStore(VectorStoreContract):
    @pytest.fixture
    def store(self, tmp_path: Path) -> VectorStore:
        pytest.importorskip("lancedb")
        from feedback_manager.storage.vector.lancedb import LanceDBVectorStore

        return LanceDBVectorStore(str(tmp_path / "vectors"))

    def test_capabilities(self, tmp_path: Path) -> None:
        pytest.importorskip("lancedb")
        from feedback_manager.storage.vector.lancedb import LanceDBVectorStore

        store = LanceDBVectorStore(str(tmp_path))
        assert store.uri == str(tmp_path)
        assert store.capabilities.metadata_filtering
        assert store.capabilities.index_management

    async def test_create_index_keeps_search_scoped_and_exact(self, store: VectorStore) -> None:
        records = [make_record(f"r{i:03}", (1.0, i / 64)) for i in range(300)]
        records.append(make_record("r000", scope=OTHER_TENANT))
        await store.upsert_many(records)
        await store.create_index(SCOPE, SPACE)
        await store.create_index(SCOPE, SPACE)
        matches = await store.search(SCOPE, SPACE, (1.0, 0.0), limit=2)
        assert ids(matches) == ["r000", "r001"]
        assert all(match.record.scope == SCOPE for match in matches)
        assert matches[0].distance == pytest.approx(0.0, abs=1e-6)
        assert await store.get(SCOPE, SPACE, [f"r{i:03}" for i in range(300)]) == tuple(
            records[:300]
        )

    async def test_create_index_on_a_new_space_creates_its_table(self, store: VectorStore) -> None:
        await store.create_index(SCOPE, WIDE_SPACE)
        assert await store.search(SCOPE, WIDE_SPACE, (1.0, 0.0, 0.0), limit=1) == ()

    async def test_zero_vectors_never_match(self, store: VectorStore) -> None:
        await store.upsert_many([make_record("zero", (0.0, 0.0)), make_record("one")])
        assert ids(await store.search(SCOPE, SPACE, (1.0, 0.0), limit=5)) == ["one"]
        assert await store.search(SCOPE, SPACE, (0.0, 0.0), limit=5) == ()

    async def test_data_persists_across_instances_and_connections(self, tmp_path: Path) -> None:
        lancedb = pytest.importorskip("lancedb")
        from feedback_manager.storage.vector.lancedb import LanceDBVectorStore

        uri = str(tmp_path / "vectors")
        record = make_record("one")
        writer = await LanceDBVectorStore.open(uri)
        await writer.upsert(record)
        connection = await lancedb.connect_async(uri)
        reader = LanceDBVectorStore(uri, connection=connection)
        assert await reader.get(SCOPE, SPACE, ["one"]) == (record,)
        assert await reader.get(SCOPE, SPACE, ["one"]) == (record,)
        await reader.health()

    def test_sql_helpers(self) -> None:
        pytest.importorskip("lancedb")
        from feedback_manager.storage.vector import lancedb as module

        assert module.sql_string(HOSTILE) == "'a'' OR ''1''=''1'"
        with pytest.raises(ValueError, match="NUL"):
            module.sql_string("a\x00b")
        assert module.sql_identifier("tenant_id") == "tenant_id"
        for bad in ("1abc", "a b", "a;--", "", "x" * 129):
            with pytest.raises(ValueError, match="identifier"):
                module.sql_identifier(bad)
        name = module.table_name(SPACE)
        assert name.startswith(module.TABLE_PREFIX)
        assert len(name) == len(module.TABLE_PREFIX) + 16
        assert name != module.table_name(OTHER_SPACE)
        assert module.table_schema(3).field("vector").type.list_size == 3

    async def test_native_scope_filters_precede_ranking(self, tmp_path: Path) -> None:
        pytest.importorskip("lancedb")
        from feedback_manager.storage.vector.lancedb import LanceDBVectorStore

        store = LanceDBVectorStore(str(tmp_path), max_search_candidates=4)
        await store.upsert_many(
            [
                *(
                    make_record(f"private-{i}", metadata={"visibility": "agent", "agent_id": "bob"})
                    for i in range(12)
                ),
                make_record(
                    "mine",
                    (0.0, 1.0),
                    metadata={
                        "visibility": "agent",
                        "agent_id": HOSTILE,
                        "agent_type": "researcher",
                        "artifact_type": "strategy",
                    },
                ),
            ]
        )
        matches = await store.search(
            SCOPE,
            SPACE,
            (1.0, 0.0),
            limit=1,
            metadata={
                "visibility": "agent",
                "agent_id": HOSTILE,
                "agent_type": "researcher",
                "artifact_type": "strategy",
            },
        )
        assert ids(matches) == ["mine"]

    async def test_search_bounds_and_unexpected_open_errors(self, tmp_path: Path) -> None:
        pytest.importorskip("lancedb")
        from feedback_manager.learning import LearningValidationError
        from feedback_manager.storage.vector.lancedb import LanceDBVectorStore

        with pytest.raises(ValueError, match="positive"):
            LanceDBVectorStore(str(tmp_path), max_search_candidates=0)
        store = LanceDBVectorStore(str(tmp_path), max_search_candidates=2)
        with pytest.raises(ValueError, match="limit exceeds"):
            await store.search(SCOPE, SPACE, (1.0, 0.0), limit=3)
        connection = AsyncMock()
        connection.open_table.side_effect = ValueError("unexpected backend error")
        with pytest.raises(ValueError, match="unexpected"):
            await LanceDBVectorStore(str(tmp_path), connection=connection).get(SCOPE, SPACE, ["x"])
        await store.upsert_many([make_record(str(i)) for i in range(3)])
        with pytest.raises(LearningValidationError, match="max_search_candidates"):
            await store.search(SCOPE, SPACE, (1.0, 0.0), limit=1)
        with pytest.raises(LearningValidationError, match="max_search_candidates"):
            await store.search(SCOPE, SPACE, (1.0, 0.0), limit=1, metadata={"absent": True})


def test_lancedb_module_requires_the_extra() -> None:
    code = (
        "import sys\n"
        "sys.modules['lancedb'] = None\n"
        "try:\n"
        "    import feedback_manager.storage.vector.lancedb\n"
        "except ImportError as exc:\n"
        "    print(exc)\n"
        "else:\n"
        "    raise SystemExit('import unexpectedly succeeded')\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert 'pip install "feedback-manager[lancedb]"' in result.stdout
