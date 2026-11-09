"""In-memory graph store and traversal helpers shared by graph adapters."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from feedback_manager.learning.errors import LearningValidationError
from feedback_manager.storage.graph.base import (
    GraphCapabilities,
    GraphDirection,
    GraphNode,
    GraphPage,
    GraphRelationship,
    GraphStore,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from feedback_manager.learning.models import TenantScope

MAX_TRAVERSAL_DEPTH = 10
"""The deepest traversal any built-in graph store accepts."""
MAX_TRAVERSAL_NODES = 10000
MAX_TRAVERSAL_EDGES = 20000

_ScopedKey = tuple[str, str, str]


def validate_traversal(max_depth: int, limit: int, cursor: str | None) -> int:
    """Validate traversal arguments and return the page offset encoded by ``cursor``.

    Args:
        max_depth: Maximum number of hops from the start nodes.
        limit: Maximum number of nodes in one page.
        cursor: Opaque cursor returned by a previous page, or ``None``.

    Returns:
        The zero-based offset of the requested page.

    Raises:
        LearningValidationError: If an argument is out of range or the cursor is invalid.
    """
    if not 1 <= max_depth <= MAX_TRAVERSAL_DEPTH:
        raise LearningValidationError(
            f"max_depth must be between 1 and {MAX_TRAVERSAL_DEPTH}, got {max_depth}"
        )
    if limit < 1:
        raise LearningValidationError(f"limit must be at least 1, got {limit}")
    if cursor is None:
        return 0
    if not cursor.isascii() or not cursor.isdigit():
        raise LearningValidationError(f"invalid traversal cursor: {cursor!r}")
    return int(cursor)


def breadth_first(
    start_ids: Iterable[str],
    expand: Callable[[Sequence[str]], Iterable[GraphRelationship]],
    max_depth: int,
) -> tuple[dict[str, int], dict[str, GraphRelationship]]:
    """Run a bounded breadth-first search.

    Args:
        start_ids: Existing start node ids.
        expand: Returns the relationships leaving a frontier in the traversal
            direction, already filtered by scope and relationship type.
        max_depth: Maximum number of hops.

    Returns:
        The depth of every reached node and the traversed relationships by id.
    """
    depths = dict.fromkeys(start_ids, 0)
    if len(depths) > MAX_TRAVERSAL_NODES:
        raise LearningValidationError("traversal exceeded the node safety bound")
    relationships: dict[str, GraphRelationship] = {}
    frontier = sorted(depths)
    for depth in range(1, max_depth + 1):
        if not frontier:
            break
        reached: set[str] = set()
        for relationship in expand(frontier):
            relationships[relationship.relationship_id] = relationship
            if len(relationships) > MAX_TRAVERSAL_EDGES:
                raise LearningValidationError("traversal exceeded the relationship safety bound")
            for endpoint in (relationship.source_id, relationship.target_id):
                if endpoint not in depths:
                    depths[endpoint] = depth
                    if len(depths) > MAX_TRAVERSAL_NODES:
                        raise LearningValidationError("traversal exceeded the node safety bound")
                    reached.add(endpoint)
        frontier = sorted(reached)
    return depths, relationships


def page_node_ids(depths: dict[str, int], offset: int, limit: int) -> tuple[list[str], str | None]:
    """Order reached nodes by depth then id and slice one page.

    Args:
        depths: The depth of every reached node.
        offset: Zero-based offset of the page.
        limit: Maximum number of nodes in the page.

    Returns:
        The node ids of the page and the cursor of the next page, if any.
    """
    ordered = sorted(depths, key=lambda node_id: (depths[node_id], node_id))
    end = offset + limit
    return ordered[offset:end], str(end) if end < len(ordered) else None


def page_relationships(
    relationships: dict[str, GraphRelationship], node_ids: Iterable[str]
) -> tuple[GraphRelationship, ...]:
    """Return traversed relationships whose endpoints are both in a page, ordered by id."""
    included = set(node_ids)
    return tuple(
        relationships[relationship_id]
        for relationship_id in sorted(relationships)
        if relationships[relationship_id].source_id in included
        and relationships[relationship_id].target_id in included
    )


def _scope_key(scope: TenantScope) -> tuple[str, str]:
    return (scope.tenant_id, scope.namespace)


def _key(scope: TenantScope, item_id: str) -> _ScopedKey:
    return (*_scope_key(scope), item_id)


class InMemoryGraphStore(GraphStore):
    """A process-local graph store for tests, examples, and small deployments.

    Nodes and relationships are isolated by tenant and namespace. Stored
    values are deep-copied on write and read so callers cannot mutate them.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._nodes: dict[_ScopedKey, GraphNode] = {}
        self._relationships: dict[_ScopedKey, GraphRelationship] = {}

    @property
    def capabilities(self) -> GraphCapabilities:
        """Describe the supported optional operations."""
        return GraphCapabilities(
            pagination=True,
            transactions=False,
            relationship_filtering=True,
            multi_hop=True,
        )

    async def upsert_nodes(self, nodes: Sequence[GraphNode]) -> None:
        """Insert or replace nodes by scope and node id.

        Args:
            nodes: Nodes to store.
        """
        async with self._lock:
            for node in nodes:
                self._nodes[_key(node.scope, node.node_id)] = node.model_copy(deep=True)

    async def get_nodes(self, scope: TenantScope, node_ids: Sequence[str]) -> Sequence[GraphNode]:
        """Return the requested nodes present in ``scope``, in request order without duplicates.

        Args:
            scope: Tenant and namespace to read from.
            node_ids: Ids of the nodes to return.

        Returns:
            The nodes that exist.
        """
        async with self._lock:
            return [
                node.model_copy(deep=True)
                for node_id in dict.fromkeys(node_ids)
                if (node := self._nodes.get(_key(scope, node_id))) is not None
            ]

    async def delete_nodes(self, scope: TenantScope, node_ids: Sequence[str]) -> None:
        """Remove nodes and every relationship touching them; missing ids are ignored.

        Args:
            scope: Tenant and namespace to delete from.
            node_ids: Ids of the nodes to remove.
        """
        async with self._lock:
            removed = set(node_ids)
            for node_id in removed:
                self._nodes.pop(_key(scope, node_id), None)
            for key, relationship in list(self._relationships.items()):
                if key[:2] == _scope_key(scope) and (
                    relationship.source_id in removed or relationship.target_id in removed
                ):
                    del self._relationships[key]

    async def upsert_relationships(self, relationships: Sequence[GraphRelationship]) -> None:
        """Insert or replace relationships after checking every endpoint exists.

        Args:
            relationships: Relationships to store.

        Raises:
            LearningValidationError: If an endpoint node is missing from the
                relationship's scope. Nothing is written in that case.
        """
        async with self._lock:
            for relationship in relationships:
                for endpoint in (relationship.source_id, relationship.target_id):
                    if _key(relationship.scope, endpoint) not in self._nodes:
                        raise LearningValidationError(
                            f"relationship {relationship.relationship_id!r} references "
                            f"node {endpoint!r} that does not exist in its scope"
                        )
            for relationship in relationships:
                key = _key(relationship.scope, relationship.relationship_id)
                self._relationships[key] = relationship.model_copy(deep=True)

    async def delete_relationships(
        self, scope: TenantScope, relationship_ids: Sequence[str]
    ) -> None:
        """Remove relationships; missing ids are ignored.

        Args:
            scope: Tenant and namespace to delete from.
            relationship_ids: Ids of the relationships to remove.
        """
        async with self._lock:
            for relationship_id in relationship_ids:
                self._relationships.pop(_key(scope, relationship_id), None)

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

        Nodes are ordered by depth, then node id; start nodes have depth zero.
        The page includes the traversed relationships whose endpoints are
        both on the page.

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
        types = frozenset(relationship_types)
        async with self._lock:
            scoped = [
                relationship
                for key, relationship in self._relationships.items()
                if key[:2] == _scope_key(scope)
                and (not types or relationship.relationship_type in types)
            ]

            def expand(frontier: Sequence[str]) -> list[GraphRelationship]:
                members = set(frontier)
                return [
                    relationship
                    for relationship in scoped
                    if (
                        direction is not GraphDirection.INCOMING
                        and relationship.source_id in members
                    )
                    or (
                        direction is not GraphDirection.OUTGOING
                        and relationship.target_id in members
                    )
                ]

            starts = [
                node_id
                for node_id in dict.fromkeys(node_ids)
                if _key(scope, node_id) in self._nodes
            ]
            depths, traversed = breadth_first(starts, expand, max_depth)
            page_ids, next_cursor = page_node_ids(depths, offset, limit)
            return GraphPage(
                nodes=tuple(
                    self._nodes[_key(scope, node_id)].model_copy(deep=True) for node_id in page_ids
                ),
                relationships=tuple(
                    relationship.model_copy(deep=True)
                    for relationship in page_relationships(traversed, page_ids)
                ),
                next_cursor=next_cursor,
            )

    async def health(self) -> None:
        """Report healthy; the in-memory store is always available."""


__all__ = [
    "MAX_TRAVERSAL_DEPTH",
    "InMemoryGraphStore",
    "breadth_first",
    "page_node_ids",
    "page_relationships",
    "validate_traversal",
]
