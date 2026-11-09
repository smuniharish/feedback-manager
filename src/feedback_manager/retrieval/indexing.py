"""Rebuildable vector and graph projections of authoritative artifacts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from feedback_manager.embeddings.providers import validate_embeddings
from feedback_manager.learning.errors import LearningScopeError, UnsupportedCapabilityError
from feedback_manager.learning.models import ArtifactStatus, TenantScope
from feedback_manager.observability.operations import operation
from feedback_manager.storage.artifacts.base import ArtifactQuery
from feedback_manager.storage.graph.base import GraphNode, GraphRelationship
from feedback_manager.storage.vector.base import VectorRecord

if TYPE_CHECKING:
    from uuid import UUID

    from feedback_manager.embeddings.base import EmbeddingProvider
    from feedback_manager.learning.models import LearnedArtifact
    from feedback_manager.observability.operations import OperationObserver
    from feedback_manager.storage.artifacts.base import ArtifactRepository
    from feedback_manager.storage.graph.base import GraphStore
    from feedback_manager.storage.vector.base import VectorStore


def artifact_node_id(artifact_id: UUID) -> str:
    """Return the stable graph node ID for an artifact."""
    return f"artifact:{artifact_id}"


class ArtifactIndexer:
    """Project artifacts using an explicitly selected embedding space.

    The repository remains authoritative. Writes are idempotent, not atomic across
    stores. Retry failed indexing or run ``reindex`` to repair missing projections.
    Reindex into a new provider's space before switching the retriever to it;
    retain the old space for rollback. No implicit cross-space conversion occurs.
    """

    def __init__(
        self,
        repository: ArtifactRepository,
        vectors: VectorStore,
        provider: EmbeddingProvider,
        *,
        graph: GraphStore | None = None,
        observer: OperationObserver | None = None,
    ) -> None:
        self.repository = repository
        self.vectors = vectors
        self.provider = provider
        self.graph = graph
        self.observer = observer

    async def drain_intents(self, scope: TenantScope, *, limit: int = 100) -> int:
        """Repair a bounded outbox page; the host serializes projectors per scope.

        A failed vector/graph operation leaves its intent pending. Replaying a
        partially written projection is idempotent and always uses current truth.
        """
        if not self.repository.atomic_projection_intents:
            raise UnsupportedCapabilityError("repository has no atomic projection outbox")
        if not 1 <= limit <= 1000:
            raise ValueError("projection limit must be between 1 and 1000")
        intents = await self.repository.pending_projections(scope, limit=limit)
        if len(intents) > limit:
            raise LearningScopeError("projection outbox exceeded the requested bound")
        for intent in intents:
            if (
                intent.scope.tenant_id != scope.tenant_id
                or intent.scope.namespace != scope.namespace
            ):
                raise LearningScopeError("projection outbox returned a foreign scope")
            await self.index(scope, intent.artifact_id)
            await self.repository.acknowledge_projection(scope, intent.intent_id)
        return len(intents)

    async def index(self, scope: TenantScope, artifact_id: UUID) -> None:
        """Refresh one artifact's current projections, deleting non-active projections."""
        with operation(self.observer, "learning.projection"):
            await self._index(scope, artifact_id)

    async def _index(self, scope: TenantScope, artifact_id: UUID) -> None:
        with operation(self.observer, "database.artifact.read"):
            artifact = await self.repository.get(scope, artifact_id)
        if (
            artifact.scope.tenant_id != scope.tenant_id
            or artifact.scope.namespace != scope.namespace
        ):
            raise LearningScopeError("artifact repository returned an out-of-scope result")
        record_id = str(artifact.artifact_id)
        if artifact.status is not ArtifactStatus.ACTIVE:
            with operation(self.observer, "database.vector.write"):
                await self.vectors.delete(scope, self.provider.space, [record_id])
            if self.graph is not None:
                with operation(self.observer, "database.graph.write"):
                    await self.graph.delete_nodes(scope, [artifact_node_id(artifact.artifact_id)])
            return
        document = f"{artifact.title}\n{artifact.description}\n{artifact.content}"
        with operation(self.observer, "embedding.documents"):
            vectors = validate_embeddings(
                self.provider.space, await self.provider.embed_documents([document]), expected=1
            )
        record = VectorRecord(
            record_id=record_id,
            scope=TenantScope(tenant_id=scope.tenant_id, namespace=scope.namespace),
            artifact_id=artifact.artifact_id,
            artifact_version=artifact.version,
            space=self.provider.space,
            vector=tuple(vectors[0]),
            metadata={
                "artifact_type": artifact.artifact_type.value,
                "visibility": artifact.scope.visibility.value,
                "agent_id": artifact.scope.agent_id,
                "agent_type": artifact.scope.agent_type,
            },
        )
        with operation(self.observer, "database.vector.write"):
            await self.vectors.upsert(record)
        if self.graph is not None:
            with operation(self.observer, "database.graph.write"):
                await self._graph_projection(artifact)

    async def _graph_projection(self, artifact: LearnedArtifact) -> None:
        assert self.graph is not None
        scope = TenantScope(tenant_id=artifact.scope.tenant_id, namespace=artifact.scope.namespace)
        node_id = artifact_node_id(artifact.artifact_id)
        # Remove obsolete edges when an artifact's applicability changes.
        await self.graph.delete_nodes(scope, [node_id])
        nodes = [
            GraphNode(
                node_id=node_id,
                scope=scope,
                node_type="artifact",
                properties={
                    "artifact_id": str(artifact.artifact_id),
                    "artifact_version": artifact.version,
                },
            )
        ]
        relationships = []
        for field in ("task_type", "tool", "workflow"):
            value = getattr(artifact.scope, field)
            if value is not None:
                entity_id = f"{field}:{value}"
                nodes.append(GraphNode(node_id=entity_id, scope=scope, node_type=field))
                relationships.append(
                    GraphRelationship(
                        relationship_id=f"{node_id}:{entity_id}",
                        scope=scope,
                        source_id=node_id,
                        target_id=entity_id,
                        relationship_type=f"applies_to_{field}",
                    )
                )
        for evidence in artifact.evidence:
            if not evidence.valid:
                continue
            evidence_id = f"evidence:{evidence.evidence_id}"
            nodes.append(
                GraphNode(
                    node_id=evidence_id,
                    scope=scope,
                    node_type="evidence",
                    properties={
                        "feedback_id": (str(evidence.feedback_id) if evidence.feedback_id else None)
                    },
                )
            )
            relationships.append(
                GraphRelationship(
                    relationship_id=f"{node_id}:{evidence_id}",
                    scope=scope,
                    source_id=node_id,
                    target_id=evidence_id,
                    relationship_type="supported_by",
                )
            )
        await self.graph.upsert_nodes(nodes)
        await self.graph.upsert_relationships(relationships)

    async def reindex(self, scope: TenantScope, *, max_artifacts: int = 10000) -> int:
        """Repair up to ``max_artifacts`` projections; fail explicitly if the bound is exceeded."""
        if max_artifacts < 1:
            raise ValueError("max_artifacts must be positive")
        cursor = None
        seen: set[UUID] = set()
        while True:
            page = await self.repository.query(
                ArtifactQuery(
                    scope=scope, limit=min(100, max_artifacts - len(seen) + 1), cursor=cursor
                )
            )
            for artifact in page.artifacts:
                if artifact.artifact_id in seen:
                    raise LearningScopeError("artifact repository returned a duplicate artifact")
                if len(seen) == max_artifacts:
                    raise ValueError("reindex exceeded max_artifacts; increase the explicit bound")
                seen.add(artifact.artifact_id)
                await self.index(scope, artifact.artifact_id)
            if page.next_cursor is None:
                return len(seen)
            if not page.artifacts or page.next_cursor == cursor:
                raise LearningScopeError("artifact repository returned an invalid cursor")
            cursor = page.next_cursor


__all__ = ["ArtifactIndexer", "artifact_node_id"]
