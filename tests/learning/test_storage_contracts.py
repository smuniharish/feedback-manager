from uuid import uuid4

import pytest
from pydantic import ValidationError

from feedback_manager.embeddings import EmbeddingSpace
from feedback_manager.learning import TenantScope, UnsupportedCapabilityError
from feedback_manager.storage.graph import (
    GraphCapabilities,
    GraphDirection,
    GraphNode,
    GraphPage,
    GraphRelationship,
    GraphStore,
)
from feedback_manager.storage.vector import VectorCapabilities, VectorRecord, VectorStore


async def test_contract_convenience_methods_delegate_batches() -> None:
    class Vector(VectorStore):
        @property
        def capabilities(self):
            return VectorCapabilities()

        async def upsert_many(self, records):
            self.written = records

        async def get(self, scope, space, record_ids):
            return []

        async def search(self, scope, space, vector, *, limit, metadata=None):
            return []

        async def delete(self, scope, space, record_ids):
            pass

        async def health(self):
            pass

    class Graph(GraphStore):
        @property
        def capabilities(self):
            return GraphCapabilities()

        async def upsert_nodes(self, nodes):
            self.nodes = nodes

        async def get_nodes(self, scope, node_ids):
            return []

        async def delete_nodes(self, scope, node_ids):
            pass

        async def upsert_relationships(self, relationships):
            self.relationships = relationships

        async def delete_relationships(self, scope, relationship_ids):
            pass

        async def traverse(
            self,
            scope,
            node_ids,
            *,
            relationship_types=(),
            direction=GraphDirection.OUTGOING,
            max_depth=1,
            limit=100,
            cursor=None,
        ):
            return GraphPage(nodes=(), relationships=())

        async def health(self):
            pass

    scope = TenantScope(tenant_id="tenant")
    space = EmbeddingSpace(provider="example", model="model", revision="1", dimensions=2)
    record = VectorRecord(
        record_id="one",
        scope=scope,
        artifact_id=uuid4(),
        artifact_version=1,
        space=space,
        vector=(0.5, 0.5),
    )
    vector = Vector()
    await vector.upsert(record)
    assert vector.written == (record,)
    with pytest.raises(UnsupportedCapabilityError, match="index management"):
        await vector.create_index(scope, space)
    with pytest.raises(ValidationError, match="dimensions"):
        VectorRecord.model_validate({**record.model_dump(), "vector": [1.0]})
    graph = Graph()
    node = GraphNode(node_id="one", scope=scope, node_type="Artifact")
    edge = GraphRelationship(
        relationship_id="edge",
        scope=scope,
        source_id="one",
        target_id="two",
        relationship_type="SUPPORTS",
    )
    await graph.upsert_node(node)
    await graph.upsert_relationship(edge)
    assert graph.nodes == (node,)
    assert graph.relationships == (edge,)
