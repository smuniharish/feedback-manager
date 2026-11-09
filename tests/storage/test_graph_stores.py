from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from feedback_manager.learning import LearningError, LearningValidationError, TenantScope
from feedback_manager.learning.errors import UnsupportedCapabilityError
from feedback_manager.storage.graph import (
    GraphCapabilities,
    GraphDirection,
    GraphNode,
    GraphRelationship,
    GraphStore,
    InMemoryGraphStore,
)
from feedback_manager.storage.graph.memory import MAX_TRAVERSAL_DEPTH

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

    from feedback_manager.core._types import JsonObject
    from feedback_manager.storage.graph.ladybug import LadybugGraphStore

SCOPE = TenantScope(tenant_id="tenant-a")
OTHER_TENANT = TenantScope(tenant_id="tenant-b")
OTHER_NAMESPACE = TenantScope(tenant_id="tenant-a", namespace="other")
INJECTIONS = ("x' OR 1=1 //", "})-[r]-() DETACH DELETE r //", 'a"b\\c', "$keys")


def node(
    node_id: str,
    *,
    scope: TenantScope = SCOPE,
    node_type: str = "Artifact",
    properties: JsonObject | None = None,
) -> GraphNode:
    return GraphNode(node_id=node_id, scope=scope, node_type=node_type, properties=properties or {})


def rel(
    relationship_id: str,
    source_id: str,
    target_id: str,
    *,
    scope: TenantScope = SCOPE,
    relationship_type: str = "KNOWS",
    properties: JsonObject | None = None,
) -> GraphRelationship:
    return GraphRelationship(
        relationship_id=relationship_id,
        scope=scope,
        source_id=source_id,
        target_id=target_id,
        relationship_type=relationship_type,
        properties=properties or {},
    )


def ladybug_store_class() -> type[LadybugGraphStore]:
    pytest.importorskip("ladybug")
    from feedback_manager.storage.graph.ladybug import LadybugGraphStore

    return LadybugGraphStore


@pytest.fixture(params=["memory", "ladybug"])
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[GraphStore]:
    if request.param == "memory":
        yield InMemoryGraphStore()
        return
    async with ladybug_store_class()(tmp_path / "graph.lbug") as graph:
        yield graph


async def seed(store: GraphStore, scope: TenantScope = SCOPE) -> None:
    """Build a -KNOWS-> b -KNOWS-> c -KNOWS-> f, a -LIKES-> d, and e -KNOWS-> a."""
    await store.upsert_nodes([node(node_id, scope=scope) for node_id in "abcdef"])
    await store.upsert_relationships(
        [
            rel("ab", "a", "b", scope=scope),
            rel("bc", "b", "c", scope=scope),
            rel("cf", "c", "f", scope=scope),
            rel("ad", "a", "d", scope=scope, relationship_type="LIKES"),
            rel("ea", "e", "a", scope=scope),
        ]
    )


def ids(items: object) -> list[str]:
    return [
        item.node_id if isinstance(item, GraphNode) else item.relationship_id
        for item in items  # type: ignore[attr-defined]
    ]


async def test_capabilities_are_declared(store: GraphStore) -> None:
    capabilities = store.capabilities
    assert isinstance(capabilities, GraphCapabilities)
    assert capabilities.pagination
    assert capabilities.relationship_filtering
    assert capabilities.multi_hop
    assert capabilities.transactions is not isinstance(store, InMemoryGraphStore)


async def test_node_upsert_is_idempotent_and_updates(store: GraphStore) -> None:
    first = node("one", properties={"weight": 1, "tags": ["x"], "nested": {"k": None}})
    await store.upsert_node(first)
    await store.upsert_node(first)
    assert await store.get_nodes(SCOPE, ["one"]) == [first]

    updated = node("one", node_type="Lesson", properties={"weight": 2.5})
    await store.upsert_nodes([updated, node("two")])
    assert list(await store.get_nodes(SCOPE, ["two", "missing", "one", "two"])) == [
        node("two"),
        updated,
    ]
    assert list(await store.get_nodes(SCOPE, [])) == []


async def test_returned_nodes_do_not_alias_storage(store: GraphStore) -> None:
    original = node("one", properties={"items": [1]})
    await store.upsert_node(original)
    original.properties["items"] = [2]
    (stored,) = await store.get_nodes(SCOPE, ["one"])
    stored.properties["items"] = [3]
    assert (await store.get_nodes(SCOPE, ["one"]))[0].properties == {"items": [1]}


async def test_empty_batches_are_noops(store: GraphStore) -> None:
    await store.upsert_nodes([])
    await store.upsert_relationships([])
    await store.delete_nodes(SCOPE, [])
    await store.delete_relationships(SCOPE, [])
    assert list(await store.get_nodes(SCOPE, ["a"])) == []


@pytest.mark.parametrize("other", [OTHER_TENANT, OTHER_NAMESPACE])
async def test_every_operation_is_scope_isolated(store: GraphStore, other: TenantScope) -> None:
    await seed(store)
    await seed(store, other)
    assert list(await store.get_nodes(other, ["a"])) == [node("a", scope=other)]

    await store.delete_relationships(other, ["ab"])
    await store.delete_nodes(other, ["c"])
    assert ids(await store.get_nodes(SCOPE, list("abcdef"))) == list("abcdef")
    page = await store.traverse(SCOPE, ["a"], max_depth=3)
    assert ids(page.nodes) == ["a", "b", "d", "c", "f"]
    assert ids(page.relationships) == ["ab", "ad", "bc", "cf"]
    assert all(item.scope == SCOPE for item in (*page.nodes, *page.relationships))

    other_page = await store.traverse(other, ["a"], max_depth=3)
    assert ids(other_page.nodes) == ["a", "d"]
    assert ids(other_page.relationships) == ["ad"]

    await store.upsert_node(node("only-here"))
    with pytest.raises(LearningValidationError, match="only-here"):
        await store.upsert_relationship(rel("cross", "a", "only-here", scope=other))
    assert ids((await store.traverse(other, ["a"])).relationships) == ["ad"]
    assert ids((await store.traverse(other, ["only-here"])).nodes) == []


async def test_relationship_endpoints_must_exist_and_batches_are_atomic(store: GraphStore) -> None:
    await store.upsert_nodes([node("a"), node("b")])
    with pytest.raises(LearningValidationError, match="'missing'"):
        await store.upsert_relationship(rel("r", "missing", "b"))
    with pytest.raises(LearningValidationError, match="'gone'"):
        await store.upsert_relationships([rel("ok", "a", "b"), rel("bad", "a", "gone")])
    page = await store.traverse(SCOPE, ["a"], direction=GraphDirection.BOTH)
    assert ids(page.nodes) == ["a"]
    assert page.relationships == ()


async def test_relationship_upsert_is_idempotent_and_can_move(store: GraphStore) -> None:
    await store.upsert_nodes([node("a"), node("b"), node("c")])
    first = rel("r", "a", "b", properties={"score": 1})
    await store.upsert_relationship(first)
    await store.upsert_relationship(first)
    assert (await store.traverse(SCOPE, ["a"])).relationships == (first,)

    moved = rel("r", "a", "c", relationship_type="LIKES", properties={"score": 2})
    await store.upsert_relationship(moved)
    page = await store.traverse(SCOPE, ["a"])
    assert ids(page.nodes) == ["a", "c"]
    assert page.relationships == (moved,)


async def test_self_loops_are_supported(store: GraphStore) -> None:
    await store.upsert_node(node("a"))
    loop = rel("loop", "a", "a")
    await store.upsert_relationship(loop)
    page = await store.traverse(SCOPE, ["a"], direction=GraphDirection.BOTH, max_depth=2)
    assert ids(page.nodes) == ["a"]
    assert page.relationships == (loop,)


async def test_delete_nodes_cascades_and_deletes_are_idempotent(store: GraphStore) -> None:
    await seed(store)
    await store.delete_nodes(SCOPE, ["b", "missing"])
    await store.delete_nodes(SCOPE, ["b"])
    assert ids(await store.get_nodes(SCOPE, list("abc"))) == ["a", "c"]
    page = await store.traverse(SCOPE, ["a", "c"], direction=GraphDirection.BOTH)
    assert ids(page.nodes) == ["a", "c", "d", "e", "f"]
    assert ids(page.relationships) == ["ad", "cf", "ea"]

    await store.delete_relationships(SCOPE, ["ad", "missing"])
    await store.delete_relationships(SCOPE, ["ad"])
    page = await store.traverse(SCOPE, ["a"], direction=GraphDirection.BOTH)
    assert ids(page.relationships) == ["ea"]


@pytest.mark.parametrize(
    ("direction", "types", "depth", "nodes", "relationships"),
    [
        (GraphDirection.OUTGOING, (), 1, "abd", ["ab", "ad"]),
        (GraphDirection.OUTGOING, ("KNOWS",), 1, "ab", ["ab"]),
        (GraphDirection.OUTGOING, ("LIKES", "LIKES"), 2, "ad", ["ad"]),
        (GraphDirection.OUTGOING, ("MISSING",), 2, "a", []),
        (GraphDirection.OUTGOING, (), 2, "abdc", ["ab", "ad", "bc"]),
        (GraphDirection.INCOMING, (), 3, "ae", ["ea"]),
        (GraphDirection.BOTH, (), 1, "abde", ["ab", "ad", "ea"]),
        (GraphDirection.BOTH, ("KNOWS",), 2, "abec", ["ab", "bc", "ea"]),
        (GraphDirection.BOTH, (), MAX_TRAVERSAL_DEPTH, "abdecf", ["ab", "ad", "bc", "cf", "ea"]),
    ],
)
async def test_traverse_direction_types_and_depth(
    store: GraphStore,
    direction: GraphDirection,
    types: tuple[str, ...],
    depth: int,
    nodes: str,
    relationships: list[str],
) -> None:
    await seed(store)
    page = await store.traverse(
        SCOPE, ["a"], relationship_types=types, direction=direction, max_depth=depth
    )
    assert ids(page.nodes) == list(nodes)
    assert ids(page.relationships) == relationships
    assert page.next_cursor is None


async def test_traverse_orders_multiple_starts_and_ignores_unknown(store: GraphStore) -> None:
    await seed(store)
    page = await store.traverse(SCOPE, ["e", "missing", "c", "e"], max_depth=1)
    assert ids(page.nodes) == ["c", "e", "a", "f"]
    assert ids(page.relationships) == ["cf", "ea"]
    empty = await store.traverse(SCOPE, ["missing"])
    assert (empty.nodes, empty.relationships, empty.next_cursor) == ((), (), None)
    assert (await store.traverse(SCOPE, [])).nodes == ()


async def test_traverse_paginates_with_opaque_cursor(store: GraphStore) -> None:
    await seed(store)
    first = await store.traverse(SCOPE, ["a"], max_depth=3, limit=2)
    assert ids(first.nodes) == ["a", "b"]
    assert ids(first.relationships) == ["ab"]
    assert first.next_cursor is not None

    second = await store.traverse(SCOPE, ["a"], max_depth=3, limit=2, cursor=first.next_cursor)
    assert ids(second.nodes) == ["d", "c"]
    assert second.relationships == ()
    assert second.next_cursor is not None

    third = await store.traverse(SCOPE, ["a"], max_depth=3, limit=2, cursor=second.next_cursor)
    assert ids(third.nodes) == ["f"]
    assert third.next_cursor is None

    exact = await store.traverse(SCOPE, ["a"], max_depth=3, limit=5)
    assert exact.next_cursor is None
    beyond = await store.traverse(SCOPE, ["a"], max_depth=3, limit=2, cursor="99")
    assert (beyond.nodes, beyond.next_cursor) == ((), None)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_depth": 0}, "max_depth"),
        ({"max_depth": MAX_TRAVERSAL_DEPTH + 1}, "max_depth"),
        ({"limit": 0}, "limit"),
        ({"cursor": "abc"}, "cursor"),
        ({"cursor": "-1"}, "cursor"),
        ({"cursor": ""}, "cursor"),
        ({"cursor": "\u0661"}, "cursor"),
    ],
)
async def test_traverse_rejects_invalid_arguments(
    store: GraphStore, kwargs: dict[str, object], message: str
) -> None:
    await seed(store)
    with pytest.raises(LearningValidationError, match=message):
        await store.traverse(SCOPE, ["a"], **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", INJECTIONS)
async def test_injection_like_values_are_stored_and_matched_literally(
    store: GraphStore, value: str
) -> None:
    scope = TenantScope(tenant_id=value, namespace=value)
    await store.upsert_nodes(
        [
            node(value, scope=scope, node_type=value, properties={value: value}),
            node("x", scope=scope),
        ]
    )
    await store.upsert_node(node(value, scope=SCOPE))
    edge = rel(value, value, "x", scope=scope, relationship_type=value, properties={value: [value]})
    await store.upsert_relationship(edge)

    (stored,) = await store.get_nodes(scope, [value])
    assert stored == node(value, scope=scope, node_type=value, properties={value: value})
    page = await store.traverse(scope, [value], relationship_types=[value])
    assert ids(page.nodes) == [value, "x"]
    assert page.relationships == (edge,)
    assert (await store.traverse(scope, [value], relationship_types=["KNOWS"])).relationships == ()

    await store.delete_relationships(scope, [value])
    await store.delete_nodes(scope, [value])
    assert list(await store.get_nodes(scope, [value, "x"])) == [node("x", scope=scope)]
    assert list(await store.get_nodes(SCOPE, [value])) == [node(value, scope=SCOPE)]


async def test_health(store: GraphStore) -> None:
    await store.health()


async def test_concurrent_writes_are_serialized(store: GraphStore) -> None:
    import asyncio

    await asyncio.gather(*(store.upsert_node(node(f"n{index}")) for index in range(10)))
    assert len(await store.get_nodes(SCOPE, [f"n{index}" for index in range(10)])) == 10


async def test_ladybug_persists_across_reopen_and_read_only_rejects_writes(tmp_path: Path) -> None:
    store_class = ladybug_store_class()
    path = tmp_path / "graph.lbug"
    async with store_class(str(path)) as store:
        assert not store.read_only
        await seed(store)

    reader = store_class(path, read_only=True)
    try:
        assert reader.read_only
        assert reader.capabilities.transactions is not False
        assert ids(await reader.get_nodes(SCOPE, ["a", "f"])) == ["a", "f"]
        page = await reader.traverse(SCOPE, ["a"], max_depth=2)
        assert ids(page.relationships) == ["ab", "ad", "bc"]
        await reader.health()
        writes: list[Callable[[], object]] = [
            lambda: reader.upsert_node(node("z")),
            lambda: reader.upsert_nodes([]),
            lambda: reader.upsert_relationship(rel("ab", "a", "b")),
            lambda: reader.delete_nodes(SCOPE, ["a"]),
            lambda: reader.delete_relationships(SCOPE, ["ab"]),
        ]
        for write in writes:
            with pytest.raises(UnsupportedCapabilityError, match="read-only"):
                await write()  # type: ignore[misc]
    finally:
        reader.close()

    async with store_class(path) as store:
        assert ids(await store.get_nodes(SCOPE, list("abcdef"))) == list("abcdef")


async def test_ladybug_read_only_without_schema_reads_empty(tmp_path: Path) -> None:
    store_class = ladybug_store_class()
    path = tmp_path / "empty.lbug"
    store_class(path).close()
    async with store_class(path, read_only=True) as reader:
        assert list(await reader.get_nodes(SCOPE, ["a"])) == []
        assert (await reader.traverse(SCOPE, ["a"])).nodes == ()
        assert list(await reader.get_nodes(SCOPE, ["a"])) == []


async def test_ladybug_read_only_requires_existing_database(tmp_path: Path) -> None:
    store_class = ladybug_store_class()
    with pytest.raises(RuntimeError, match="READ ONLY"):
        store_class(tmp_path / "missing.lbug", read_only=True)


async def test_ladybug_closed_store_rejects_operations(tmp_path: Path) -> None:
    store = ladybug_store_class()(tmp_path / "graph.lbug")
    store.close()
    store.close()
    with pytest.raises(LearningError, match="closed"):
        await store.health()
    with pytest.raises(LearningError, match="closed"):
        await store.get_nodes(SCOPE, ["a"])


def test_ladybug_module_requires_extra() -> None:
    code = (
        "import sys\n"
        "sys.modules['ladybug'] = None\n"
        "try:\n"
        "    import feedback_manager.storage.graph.ladybug\n"
        "except ImportError as exc:\n"
        "    print(exc)\n"
        "else:\n"
        "    raise SystemExit('import unexpectedly succeeded')\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert 'pip install "feedback-manager[ladybugdb]"' in result.stdout
