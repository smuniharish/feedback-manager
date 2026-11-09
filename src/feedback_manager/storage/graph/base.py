"""Graph storage contracts for scoped learning relationships."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from feedback_manager.core._types import Identifier, JsonObject
from feedback_manager.learning.models import TenantScope

if TYPE_CHECKING:
    from collections.abc import Sequence


class GraphCapabilities(BaseModel):
    """Optional operations supported by a graph adapter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pagination: bool = False
    transactions: bool = False
    relationship_filtering: bool = False
    multi_hop: bool = False


class GraphNode(BaseModel):
    """A scoped graph projection node with an extensible type."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    node_id: Identifier
    scope: TenantScope
    node_type: Identifier
    properties: JsonObject = Field(default_factory=dict)


class GraphRelationship(BaseModel):
    """A scoped, directed relationship between two projected nodes."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    relationship_id: Identifier
    scope: TenantScope
    source_id: Identifier
    target_id: Identifier
    relationship_type: Identifier
    properties: JsonObject = Field(default_factory=dict)


class GraphDirection(StrEnum):
    """Direction of a graph traversal."""

    OUTGOING = "outgoing"
    INCOMING = "incoming"
    BOTH = "both"


class GraphPage(BaseModel):
    """A bounded subgraph returned by traversal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    nodes: tuple[GraphNode, ...]
    relationships: tuple[GraphRelationship, ...]
    next_cursor: Identifier | None = None


class GraphStore(ABC):
    """Store and traverse tenant-scoped graph projections."""

    @property
    @abstractmethod
    def capabilities(self) -> GraphCapabilities:
        """Describe optional graph operations supported by this adapter."""

    async def upsert_node(self, node: GraphNode) -> None:
        """Idempotently insert or update a node."""
        await self.upsert_nodes((node,))

    @abstractmethod
    async def upsert_nodes(self, nodes: Sequence[GraphNode]) -> None:
        """Idempotently insert or update a batch of nodes."""

    @abstractmethod
    async def get_nodes(self, scope: TenantScope, node_ids: Sequence[str]) -> Sequence[GraphNode]:
        """Return nodes present in the requested tenant and namespace."""

    @abstractmethod
    async def delete_nodes(self, scope: TenantScope, node_ids: Sequence[str]) -> None:
        """Remove nodes and their relationships within the requested scope."""

    async def upsert_relationship(self, relationship: GraphRelationship) -> None:
        """Idempotently insert or update one relationship."""
        await self.upsert_relationships((relationship,))

    @abstractmethod
    async def upsert_relationships(self, relationships: Sequence[GraphRelationship]) -> None:
        """Insert or update relationships whose endpoints share their scope."""

    @abstractmethod
    async def delete_relationships(
        self, scope: TenantScope, relationship_ids: Sequence[str]
    ) -> None:
        """Idempotently remove relationships within the requested scope."""

    @abstractmethod
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
        """Return a bounded scoped subgraph reachable from the supplied nodes."""

    @abstractmethod
    async def health(self) -> None:
        """Check graph availability and raise an error on failure."""


__all__ = [
    "GraphCapabilities",
    "GraphDirection",
    "GraphNode",
    "GraphPage",
    "GraphRelationship",
    "GraphStore",
]
