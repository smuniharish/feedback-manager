"""LadybugDB graph store, installed with ``feedback-manager[ladybugdb]``.

LadybugDB is an embedded property-graph database. A database file can be
opened by one read-write process or by many read-only processes. Every query
is parameterized; node and relationship types are stored as properties, so no
user value is ever interpolated into Cypher.
"""

from __future__ import annotations

try:
    import ladybug
except ImportError as exc:  # pragma: no cover - exercised in a subprocess test
    raise ImportError(
        "feedback_manager.storage.graph.ladybug needs LadybugDB; "
        'install it with: pip install "feedback-manager[ladybugdb]"'
    ) from exc

import asyncio
import json
import os
from typing import TYPE_CHECKING, Any, Self, cast

from feedback_manager.learning.errors import (
    LearningError,
    LearningValidationError,
    UnsupportedCapabilityError,
)
from feedback_manager.learning.models import TenantScope
from feedback_manager.storage.graph.base import (
    GraphCapabilities,
    GraphDirection,
    GraphNode,
    GraphPage,
    GraphRelationship,
    GraphStore,
)
from feedback_manager.storage.graph.memory import (
    MAX_TRAVERSAL_EDGES,
    breadth_first,
    page_node_ids,
    page_relationships,
    validate_traversal,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from types import TracebackType

    from feedback_manager.core._types import JsonObject

_NODE_TABLE = "FMNode"
_REL_TABLE = "FMRel"

_SCHEMA = (
    "CREATE NODE TABLE IF NOT EXISTS FMNode("
    "key STRING, tenant_id STRING, namespace STRING, node_id STRING, "
    "node_type STRING, properties STRING, PRIMARY KEY(key))",
    "CREATE REL TABLE IF NOT EXISTS FMRel("
    "FROM FMNode TO FMNode, key STRING, relationship_id STRING, tenant_id STRING, "
    "namespace STRING, relationship_type STRING, properties STRING)",
)
_SHOW_TABLES = "CALL show_tables() RETURN name"
_HEALTH = "RETURN 1"

_UPSERT_NODE = (
    "MERGE (n:FMNode {key: $key}) "
    "SET n.tenant_id = $tenant_id, n.namespace = $namespace, n.node_id = $node_id, "
    "n.node_type = $node_type, n.properties = $properties"
)
_GET_NODES = (
    "MATCH (n:FMNode) "
    "WHERE n.key IN $keys AND n.tenant_id = $tenant_id AND n.namespace = $namespace "
    "RETURN n.node_id, n.node_type, n.properties"
)
_DELETE_NODES = (
    "MATCH (n:FMNode) "
    "WHERE n.key IN $keys AND n.tenant_id = $tenant_id AND n.namespace = $namespace "
    "DETACH DELETE n"
)
_EXISTING_NODE_KEYS = "MATCH (n:FMNode) WHERE n.key IN $keys RETURN n.key"
_DETACH_MOVED_RELATIONSHIP = (
    "MATCH (a:FMNode)-[r:FMRel]->(b:FMNode) "
    "WHERE r.key = $key AND (a.key <> $source_key OR b.key <> $target_key) "
    "DELETE r"
)
_UPSERT_RELATIONSHIP = (
    "MATCH (a:FMNode {key: $source_key}), (b:FMNode {key: $target_key}) "
    "MERGE (a)-[r:FMRel {key: $key}]->(b) "
    "SET r.relationship_id = $relationship_id, r.tenant_id = $tenant_id, "
    "r.namespace = $namespace, r.relationship_type = $relationship_type, "
    "r.properties = $properties"
)
_DELETE_RELATIONSHIPS = (
    "MATCH (:FMNode)-[r:FMRel]->(:FMNode) "
    "WHERE r.key IN $keys AND r.tenant_id = $tenant_id AND r.namespace = $namespace "
    "DELETE r"
)
_HOP_MATCH = (
    "MATCH (a:FMNode)-[r:FMRel]->(b:FMNode) "
    "WHERE r.tenant_id = $tenant_id AND r.namespace = $namespace "
    "AND a.tenant_id = $tenant_id AND a.namespace = $namespace "
    "AND b.tenant_id = $tenant_id AND b.namespace = $namespace "
)
_HOP_FROM_SOURCE = "AND a.key IN $keys "
_HOP_FROM_TARGET = "AND b.key IN $keys "
_HOP_TYPE_FILTER = "AND r.relationship_type IN $relationship_types "
_HOP_RETURN = "RETURN r.relationship_id, a.node_id, b.node_id, r.relationship_type, r.properties"


def _encode_key(scope: TenantScope, item_id: str) -> str:
    return json.dumps([scope.tenant_id, scope.namespace, item_id], ensure_ascii=False)


def _encode_properties(properties: JsonObject) -> str:
    return json.dumps(
        properties, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _decode_properties(encoded: str) -> JsonObject:
    return cast("JsonObject", json.loads(encoded))


class ReadOnlyGraphStoreError(UnsupportedCapabilityError):
    """A write was attempted on a graph store opened read-only."""


class LadybugGraphStore(GraphStore):
    """A persistent graph store backed by an embedded LadybugDB database.

    The schema is created on first use. Blocking database calls run in a
    worker thread, serialized by an `asyncio.Lock` because connections are not
    guaranteed to be thread-safe. Batch writes run in one transaction and are
    rolled back on error.

    Example:
        ```python
        async with LadybugGraphStore("graph.lbug") as store:
            await store.upsert_node(node)
        ```
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        database: ladybug.Database | None = None,
        read_only: bool = False,
        database_options: Mapping[str, Any] | None = None,
    ) -> None:
        """Open the database at ``path``, or use a database you already opened.

        Args:
            path: Database file; it is created when opened read-write.
            database: A ``ladybug.Database`` you opened and configured yourself.
                The store opens its own connection to it and never closes it;
                its ``read_only`` mode applies to the store.
            read_only: Open the database read-only, allowing other read-only
                processes to share it. Writes then raise `ReadOnlyGraphStoreError`.
            database_options: Extra ``ladybug.Database`` keyword arguments used
                when opening ``path``, such as ``buffer_pool_size`` or
                ``max_db_size``.

        Raises:
            ValueError: If neither or both of ``path`` and ``database`` are
                given, or ``database_options`` is combined with ``database`` or
                sets ``read_only``.
            RuntimeError: If LadybugDB cannot open the database, for example a
                missing database in read-only mode.
        """
        if (path is None) == (database is None):
            raise ValueError("pass exactly one of path or database")
        options = dict(database_options or {})
        if database is not None:
            if options:
                raise ValueError("database_options apply only when the store opens path")
            self._read_only = read_only or bool(database.read_only)
            self._database = database
            self._owns_database = False
        else:
            if "read_only" in options:
                raise ValueError("set read_only with the read_only argument")
            self._read_only = read_only
            self._database = ladybug.Database(
                os.fspath(cast("str | os.PathLike[str]", path)), read_only=read_only, **options
            )
            self._owns_database = True
        self._connection = ladybug.Connection(self._database)
        self._lock = asyncio.Lock()
        self._schema_checked = False
        self._has_schema = False
        self._closed = False

    @property
    def database(self) -> ladybug.Database:
        """The underlying ``ladybug.Database``."""
        return self._database

    @property
    def capabilities(self) -> GraphCapabilities:
        """Describe the supported optional operations."""
        return GraphCapabilities(
            pagination=True,
            transactions=True,
            relationship_filtering=True,
            multi_hop=True,
        )

    @property
    def read_only(self) -> bool:
        """Whether the database was opened read-only."""
        return self._read_only

    def close(self) -> None:
        """Close the connection, and the database if the store opened it.

        A database passed as ``database`` stays open. Closing twice has no effect.
        """
        if self._closed:
            return
        self._closed = True
        self._connection.close()
        if self._owns_database:
            self._database.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close after in-flight operations finish; safe to call repeatedly."""
        async with self._lock:
            await asyncio.to_thread(self.close)

    async def upsert_nodes(self, nodes: Sequence[GraphNode]) -> None:
        """Insert or update nodes in one transaction.

        Args:
            nodes: Nodes to store.

        Raises:
            ReadOnlyGraphStoreError: If the store is read-only.
        """
        self._require_writable()
        if nodes:
            await self._run(self._transaction, lambda: self._upsert_nodes(nodes))

    async def get_nodes(self, scope: TenantScope, node_ids: Sequence[str]) -> Sequence[GraphNode]:
        """Return the requested nodes present in ``scope``, in request order without duplicates.

        Args:
            scope: Tenant and namespace to read from.
            node_ids: Ids of the nodes to return.

        Returns:
            The nodes that exist.
        """
        return await self._run(self._get_nodes, scope, node_ids)

    async def delete_nodes(self, scope: TenantScope, node_ids: Sequence[str]) -> None:
        """Remove nodes and every relationship touching them; missing ids are ignored.

        Args:
            scope: Tenant and namespace to delete from.
            node_ids: Ids of the nodes to remove.

        Raises:
            ReadOnlyGraphStoreError: If the store is read-only.
        """
        self._require_writable()
        if node_ids:
            await self._run(self._delete, _DELETE_NODES, scope, node_ids)

    async def upsert_relationships(self, relationships: Sequence[GraphRelationship]) -> None:
        """Insert or update relationships in one transaction.

        Args:
            relationships: Relationships to store.

        Raises:
            ReadOnlyGraphStoreError: If the store is read-only.
            LearningValidationError: If an endpoint node is missing from the
                relationship's scope. Nothing is written in that case.
        """
        self._require_writable()
        if relationships:
            await self._run(self._transaction, lambda: self._upsert_relationships(relationships))

    async def delete_relationships(
        self, scope: TenantScope, relationship_ids: Sequence[str]
    ) -> None:
        """Remove relationships; missing ids are ignored.

        Args:
            scope: Tenant and namespace to delete from.
            relationship_ids: Ids of the relationships to remove.

        Raises:
            ReadOnlyGraphStoreError: If the store is read-only.
        """
        self._require_writable()
        if relationship_ids:
            await self._run(self._delete, _DELETE_RELATIONSHIPS, scope, relationship_ids)

    async def traverse(
        self,
        scope: TenantScope,
        node_ids: Sequence[str],
        *,
        relationship_types: Sequence[str] = (),
        direction: GraphDirection = GraphDirection.OUTGOING,
        max_depth: int = 1,
        limit: int = 100,
        cursor: str | None = None,
    ) -> GraphPage:
        """Return one page of the subgraph reachable from ``node_ids``.

        Runs a bounded breadth-first search with one parameterized query per
        hop. Nodes are ordered by depth, then node id; start nodes have depth
        zero. The page includes the traversed relationships whose endpoints
        are both on the page.

        Args:
            scope: Tenant and namespace to traverse.
            node_ids: Start node ids; ids missing from ``scope`` are ignored.
            relationship_types: Relationship types to follow; empty follows all.
            direction: Direction in which relationships are followed.
            max_depth: Maximum number of hops, from 1 to 10.
            limit: Maximum number of nodes in the page.
            cursor: ``next_cursor`` of the previous page.

        Returns:
            The requested page.

        Raises:
            LearningValidationError: If an argument or the cursor is invalid.
        """
        offset = validate_traversal(max_depth, limit, cursor)

        def traverse() -> GraphPage:
            starts = [node.node_id for node in self._get_nodes(scope, node_ids)]
            depths, traversed = breadth_first(
                starts,
                lambda frontier: self._expand(scope, frontier, relationship_types, direction),
                max_depth,
            )
            page_ids, next_cursor = page_node_ids(depths, offset, limit)
            return GraphPage(
                nodes=tuple(self._get_nodes(scope, page_ids)),
                relationships=page_relationships(traversed, page_ids),
                next_cursor=next_cursor,
            )

        return await self._run(traverse)

    async def health(self) -> None:
        """Run a trivial query.

        Raises:
            LearningError: If the store is closed.
            RuntimeError: If LadybugDB fails to answer.
        """
        await self._run(self._rows, _HEALTH, {})

    def _require_writable(self) -> None:
        if self._read_only:
            raise ReadOnlyGraphStoreError("LadybugGraphStore was opened read-only")

    async def _run[**P, R](self, func: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
        if self._closed:
            raise LearningError("LadybugGraphStore is closed")
        async with self._lock:
            if self._closed:
                raise LearningError("LadybugGraphStore is closed")
            operation = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
            try:
                return await asyncio.shield(operation)
            except asyncio.CancelledError:
                # The connection must stay locked until its worker thread exits.
                await operation
                raise

    def _rows(self, query: str, parameters: dict[str, Any]) -> list[list[Any]]:
        result = cast("ladybug.QueryResult", self._connection.execute(query, parameters))
        try:
            return cast("list[list[Any]]", result.get_all())
        finally:
            result.close()

    def _schema_ready(self) -> bool:
        if not self._schema_checked:
            if self._read_only:
                tables = {row[0] for row in self._rows(_SHOW_TABLES, {})}
                self._has_schema = {_NODE_TABLE, _REL_TABLE} <= tables
            else:
                for statement in _SCHEMA:
                    self._rows(statement, {})
                self._has_schema = True
            self._schema_checked = True
        return self._has_schema

    def _transaction(self, operation: Callable[[], None]) -> None:
        self._schema_ready()
        self._rows("BEGIN TRANSACTION", {})
        try:
            operation()
        except BaseException:
            self._rows("ROLLBACK", {})
            raise
        self._rows("COMMIT", {})

    def _upsert_nodes(self, nodes: Sequence[GraphNode]) -> None:
        for node in nodes:
            self._rows(
                _UPSERT_NODE,
                {
                    "key": _encode_key(node.scope, node.node_id),
                    "tenant_id": node.scope.tenant_id,
                    "namespace": node.scope.namespace,
                    "node_id": node.node_id,
                    "node_type": node.node_type,
                    "properties": _encode_properties(node.properties),
                },
            )

    def _upsert_relationships(self, relationships: Sequence[GraphRelationship]) -> None:
        endpoints = {
            _encode_key(relationship.scope, endpoint): (relationship, endpoint)
            for relationship in relationships
            for endpoint in (relationship.source_id, relationship.target_id)
        }
        existing = {row[0] for row in self._rows(_EXISTING_NODE_KEYS, {"keys": list(endpoints)})}
        for key, (relationship, endpoint) in endpoints.items():
            if key not in existing:
                raise LearningValidationError(
                    f"relationship {relationship.relationship_id!r} references "
                    f"node {endpoint!r} that does not exist in its scope"
                )
        for relationship in relationships:
            keys = {
                "key": _encode_key(relationship.scope, relationship.relationship_id),
                "source_key": _encode_key(relationship.scope, relationship.source_id),
                "target_key": _encode_key(relationship.scope, relationship.target_id),
            }
            self._rows(_DETACH_MOVED_RELATIONSHIP, keys)
            self._rows(
                _UPSERT_RELATIONSHIP,
                {
                    **keys,
                    "relationship_id": relationship.relationship_id,
                    "tenant_id": relationship.scope.tenant_id,
                    "namespace": relationship.scope.namespace,
                    "relationship_type": relationship.relationship_type,
                    "properties": _encode_properties(relationship.properties),
                },
            )

    def _get_nodes(self, scope: TenantScope, node_ids: Sequence[str]) -> list[GraphNode]:
        requested = list(dict.fromkeys(node_ids))
        if not requested or not self._schema_ready():
            return []
        rows = self._rows(_GET_NODES, self._scoped_keys(scope, requested))
        node_scope = TenantScope(tenant_id=scope.tenant_id, namespace=scope.namespace)
        found = {
            node_id: GraphNode(
                node_id=node_id,
                scope=node_scope,
                node_type=node_type,
                properties=_decode_properties(properties),
            )
            for node_id, node_type, properties in rows
        }
        return [found[node_id] for node_id in requested if node_id in found]

    def _delete(self, query: str, scope: TenantScope, item_ids: Sequence[str]) -> None:
        self._schema_ready()
        self._rows(query, self._scoped_keys(scope, item_ids))

    def _expand(
        self,
        scope: TenantScope,
        frontier: Sequence[str],
        relationship_types: Sequence[str],
        direction: GraphDirection,
    ) -> list[GraphRelationship]:
        anchors: list[str] = []
        if direction is not GraphDirection.INCOMING:
            anchors.append(_HOP_FROM_SOURCE)
        if direction is not GraphDirection.OUTGOING:
            anchors.append(_HOP_FROM_TARGET)
        parameters = self._scoped_keys(scope, frontier)
        parameters["edge_limit"] = MAX_TRAVERSAL_EDGES + 1
        type_filter = ""
        if relationship_types:
            type_filter = _HOP_TYPE_FILTER
            parameters["relationship_types"] = list(dict.fromkeys(relationship_types))
        relationship_scope = TenantScope(tenant_id=scope.tenant_id, namespace=scope.namespace)
        return [
            GraphRelationship(
                relationship_id=relationship_id,
                scope=relationship_scope,
                source_id=source_id,
                target_id=target_id,
                relationship_type=relationship_type,
                properties=_decode_properties(properties),
            )
            for anchor in anchors
            for relationship_id, source_id, target_id, relationship_type, properties in self._rows(
                _HOP_MATCH + anchor + type_filter + _HOP_RETURN + " LIMIT $edge_limit", parameters
            )
        ]

    @staticmethod
    def _scoped_keys(scope: TenantScope, item_ids: Sequence[str]) -> dict[str, Any]:
        return {
            "keys": [_encode_key(scope, item_id) for item_id in item_ids],
            "tenant_id": scope.tenant_id,
            "namespace": scope.namespace,
        }


__all__ = ["LadybugGraphStore", "ReadOnlyGraphStoreError"]
