"""Bounded hybrid retrieval with authoritative validation of every projection."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from feedback_manager.embeddings.providers import ensure_compatible, validate_embeddings
from feedback_manager.learning.errors import (
    ArtifactNotFoundError,
    LearningScopeError,
    UnsupportedCapabilityError,
)
from feedback_manager.learning.models import ArtifactStatus
from feedback_manager.learning.scope import is_applicable
from feedback_manager.observability.operations import operation
from feedback_manager.retrieval.base import LearnedArtifactRetriever
from feedback_manager.retrieval.indexing import artifact_node_id
from feedback_manager.retrieval.models import RetrievalRequest, RetrievalResult, RetrievedArtifact
from feedback_manager.retrieval.structured import StructuredRetriever
from feedback_manager.storage.graph.base import GraphDirection

if TYPE_CHECKING:
    from feedback_manager.core._types import JsonObject
    from feedback_manager.embeddings.base import EmbeddingProvider
    from feedback_manager.learning.models import LearnedArtifact
    from feedback_manager.learning.validation import LearningValidationPolicy
    from feedback_manager.observability.operations import OperationObserver
    from feedback_manager.storage.artifacts.base import ArtifactRepository
    from feedback_manager.storage.graph.base import GraphStore
    from feedback_manager.storage.vector.base import VectorMatch, VectorStore


class HybridRetriever(LearnedArtifactRetriever):
    """Fuse structured, cosine-vector and graph signals with confidence and evidence.

    All stores are injected. Vector and graph records are untrusted hints: current
    authoritative versions, review policy and applicability are checked again.
    Missing or obsolete projections are skipped, never returned as artifacts.
    Backend outages propagate rather than silently degrading the configured search.
    """

    def __init__(
        self,
        repository: ArtifactRepository,
        vectors: VectorStore | None,
        provider: EmbeddingProvider | None,
        *,
        graph: GraphStore | None = None,
        validation_policy: LearningValidationPolicy | None = None,
        strategy: Literal["hybrid", "vector", "graph"] = "hybrid",
        observer: OperationObserver | None = None,
    ) -> None:
        if strategy not in ("hybrid", "vector", "graph"):
            raise ValueError("unknown retrieval strategy")
        if strategy in ("hybrid", "vector") and (vectors is None or provider is None):
            raise UnsupportedCapabilityError("vector retrieval requires a store and provider")
        if strategy == "graph" and graph is None:
            raise UnsupportedCapabilityError("graph retrieval requires a GraphStore")
        self.strategy = strategy
        self.repository = repository
        self.vectors = vectors
        self.provider = provider
        self.graph = graph
        self.observer = observer
        self.structured = StructuredRetriever(repository, validation_policy=validation_policy)

    async def _search(
        self, request: RetrievalRequest, query: list[float]
    ) -> tuple[VectorMatch, ...]:
        assert self.provider is not None
        assert self.vectors is not None
        filters: list[JsonObject] = [{"visibility": "tenant"}]
        if request.agent_id is not None:
            filters.append({"visibility": "agent", "agent_id": request.agent_id})
        if request.agent_type is not None:
            filters.append({"visibility": "agent_type", "agent_type": request.agent_type})
        with operation(self.observer, "database.vector.read"):
            batches = await asyncio.gather(
                *(
                    self.vectors.search(
                        request.scope,
                        self.provider.space,
                        query,
                        limit=request.max_candidates,
                        metadata=metadata,
                    )
                    for metadata in filters
                )
            )
        if any(len(batch) > request.max_candidates for batch in batches):
            raise LearningScopeError("vector store exceeded the requested candidate bound")
        for batch in batches:
            for match in batch:
                ensure_compatible(self.provider.space, match.record.space)
                if (
                    match.record.scope.tenant_id != request.scope.tenant_id
                    or match.record.scope.namespace != request.scope.namespace
                ):
                    raise LearningScopeError("vector store returned an out-of-scope result")
        unique = {match.record.record_id: match for batch in batches for match in batch}
        return tuple(
            sorted(unique.values(), key=lambda item: (item.distance, item.record.record_id))
        )[: request.max_candidates]

    async def _current(
        self, request: RetrievalRequest, artifact_id: UUID, version: int
    ) -> LearnedArtifact | None:
        try:
            with operation(self.observer, "database.artifact.read"):
                artifact = await self.repository.get(request.scope, artifact_id)
        except ArtifactNotFoundError:
            return None
        if (
            artifact.scope.tenant_id != request.scope.tenant_id
            or artifact.scope.namespace != request.scope.namespace
        ):
            raise LearningScopeError("artifact repository returned an out-of-scope result")
        if artifact.version != version:
            return None
        if not is_applicable(
            artifact.scope,
            request.scope,
            agent_id=request.agent_id,
            agent_type=request.agent_type,
            task_type=request.task_type,
            tool=request.tool,
            workflow=request.workflow,
        ):
            return None
        if artifact.status is not ArtifactStatus.ACTIVE or (
            request.artifact_types and artifact.artifact_type not in request.artifact_types
        ):
            return None
        decision = self.structured.validation_policy.evaluate(artifact)
        if not decision.eligible or decision.confidence.confidence < request.min_confidence:
            return None
        return artifact

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        """Return ranked, reviewed artifacts within candidate and context limits."""
        with operation(self.observer, "learning.retrieval"):
            return await self._retrieve(request)

    async def _retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        depth = request.graph_depth
        if self.strategy == "graph" and depth == 0:
            depth = 1
        if depth and self.graph is None:
            raise UnsupportedCapabilityError("graph retrieval requires a GraphStore")
        structured = (
            RetrievalResult(results=())
            if self.strategy == "vector"
            else (
                await self.structured.retrieve(
                    request.model_copy(
                        update={
                            "graph_depth": 0,
                            "limit": min(100, request.max_candidates),
                            "max_context_chars": 100000,
                        }
                    )
                )
            )
        )
        items = (
            {}
            if self.strategy == "vector"
            else {item.artifact.artifact_id: item.artifact for item in structured.results}
        )
        lexical = (
            {item.artifact.artifact_id: item.relevance_score for item in structured.results}
            if self.strategy == "hybrid"
            else {}
        )
        semantic: dict[UUID, float] = {}
        graph_scores: dict[UUID, float] = {}
        truncated = structured.truncated
        if request.task and self.strategy != "graph":
            assert self.provider is not None
            assert self.vectors is not None
            with operation(self.observer, "embedding.query"):
                (query,) = validate_embeddings(
                    self.provider.space, [await self.provider.embed_query(request.task)], expected=1
                )
            matches = await self._search(request, list(query))
            truncated = truncated or len(matches) == request.max_candidates
            for match in matches:
                record = match.record
                artifact = await self._current(request, record.artifact_id, record.artifact_version)
                if artifact is None:
                    continue
                if artifact.artifact_id not in items and len(items) >= request.max_candidates:
                    truncated = True
                    continue
                items[artifact.artifact_id] = artifact
                semantic[artifact.artifact_id] = max(0.0, 1.0 - match.distance / 2)
        if depth and self.graph is not None and items and self.strategy != "vector":
            with operation(self.observer, "database.graph.read"):
                page = await self.graph.traverse(
                    request.scope,
                    [artifact_node_id(key) for key in items],
                    direction=GraphDirection.BOTH,
                    max_depth=depth,
                    limit=request.max_candidates,
                )
            if len(page.nodes) > request.max_candidates:
                raise LearningScopeError("graph store exceeded the requested candidate bound")
            truncated = truncated or page.next_cursor is not None
            for node in page.nodes:
                if (
                    node.scope.tenant_id != request.scope.tenant_id
                    or node.scope.namespace != request.scope.namespace
                ):
                    raise LearningScopeError("graph store returned an out-of-scope result")
                if node.node_type != "artifact":
                    continue
                identifier = node.properties.get("artifact_id")
                version = node.properties.get("artifact_version")
                if not isinstance(identifier, str) or not isinstance(version, int):
                    raise LearningScopeError("artifact graph node has invalid version metadata")
                artifact = await self._current(request, UUID(identifier), version)
                if artifact is None:
                    continue
                if artifact.artifact_id not in items and len(items) >= request.max_candidates:
                    truncated = True
                    continue
                items[artifact.artifact_id] = artifact
                graph_scores[artifact.artifact_id] = 1.0
        if self.strategy == "graph":
            items = {key: value for key, value in items.items() if key in graph_scores}
        candidates = []
        for identifier, artifact in items.items():
            decision = self.structured.validation_policy.evaluate(artifact)
            confidence = decision.confidence.confidence
            support = min(decision.confidence.independent_support / 2, 1)
            text = lexical.get(identifier, 0)
            vector = semantic.get(identifier, 0)
            graph = graph_scores.get(identifier, 0)
            score = 0.3 * text + 0.35 * vector + 0.1 * graph + 0.15 * confidence + 0.1 * support
            candidates.append(
                RetrievedArtifact(
                    artifact=artifact,
                    relevance_score=score,
                    confidence_score=confidence,
                    evidence_count=artifact.evidence_count,
                    explanation=(
                        f"structured: {text:.3f}",
                        f"vector cosine relevance: {vector:.3f}",
                        f"graph connection: {graph:.3f}",
                        f"evidence confidence: {confidence:.3f}",
                        f"independent support: {decision.confidence.independent_support}",
                    ),
                )
            )
        candidates.sort(key=lambda item: (-item.relevance_score, str(item.artifact.artifact_id)))
        selected = []
        chars = 0
        for item in candidates:
            if len(selected) == request.limit:
                truncated = True
                break
            size = (
                len(item.artifact.title) + 1 + len(item.artifact.content) + (2 if selected else 0)
            )
            if chars + size > request.max_context_chars:
                truncated = True
                continue
            selected.append(item)
            chars += size
        return RetrievalResult(results=tuple(selected), truncated=truncated)


class VectorRetriever(HybridRetriever):
    """Vector-only candidate selection followed by authoritative review and scope checks."""

    def __init__(
        self,
        repository: ArtifactRepository,
        vectors: VectorStore,
        provider: EmbeddingProvider,
        *,
        validation_policy: LearningValidationPolicy | None = None,
        observer: OperationObserver | None = None,
    ) -> None:
        super().__init__(
            repository,
            vectors,
            provider,
            validation_policy=validation_policy,
            strategy="vector",
            observer=observer,
        )


class GraphRetriever(HybridRetriever):
    """Graph retrieval seeded by scoped active artifacts; defaults to one hop."""

    def __init__(
        self,
        repository: ArtifactRepository,
        graph: GraphStore,
        *,
        validation_policy: LearningValidationPolicy | None = None,
        observer: OperationObserver | None = None,
    ) -> None:
        super().__init__(
            repository,
            None,
            None,
            graph=graph,
            validation_policy=validation_policy,
            strategy="graph",
            observer=observer,
        )


__all__ = ["GraphRetriever", "HybridRetriever", "VectorRetriever"]
