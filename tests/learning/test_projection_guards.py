from unittest.mock import patch

import pytest

from feedback_manager.embeddings import HashingEmbeddingProvider
from feedback_manager.learning import (
    ArtifactScope,
    ArtifactStatus,
    ArtifactType,
    LearningScopeError,
    UnsupportedCapabilityError,
)
from feedback_manager.retrieval import (
    ArtifactIndexer,
    GraphRetriever,
    HybridRetriever,
    RetrievalRequest,
    VectorRetriever,
)
from feedback_manager.storage.artifacts import ArtifactPage, InMemoryArtifactRepository
from feedback_manager.storage.graph import GraphNode, GraphPage, InMemoryGraphStore
from feedback_manager.storage.vector import InMemoryVectorStore, VectorMatch
from tests.learning.test_retrieval import active_artifact

SCOPE = ArtifactScope(tenant_id="tenant", agent_id="alice")


async def setup():
    repo, vectors, graph = InMemoryArtifactRepository(), InMemoryVectorStore(), InMemoryGraphStore()
    provider = HashingEmbeddingProvider()
    artifact = await active_artifact(repo, SCOPE)
    await ArtifactIndexer(repo, vectors, provider, graph=graph).index(SCOPE, artifact.artifact_id)
    request = RetrievalRequest(scope=SCOPE, agent_id="alice", task="validate", graph_depth=0)
    return repo, vectors, graph, provider, artifact, request


async def test_explicit_strategies_and_context_bounds():
    repo, vectors, graph, provider, artifact, request = await setup()
    assert (await VectorRetriever(repo, vectors, provider).retrieve(request)).artifacts == (
        artifact,
    )
    assert (await GraphRetriever(repo, graph).retrieve(request)).artifacts == (artifact,)
    engine_retriever = VectorRetriever(repo, vectors, provider)
    from feedback_manager.learning.engine import LearningEngine

    assert (await LearningEngine(repo, retriever=engine_retriever).retrieve(request)).artifacts
    with pytest.raises(UnsupportedCapabilityError, match="store and provider"):
        HybridRetriever(repo, None, None)
    with pytest.raises(UnsupportedCapabilityError, match="GraphStore"):
        HybridRetriever(repo, None, None, strategy="graph")
    # Runtime validation also covers callers without a type checker.
    with pytest.raises(ValueError, match="unknown"):
        HybridRetriever(repo, vectors, provider, strategy="invalid")  # type: ignore[arg-type]
    await active_artifact(repo, SCOPE, content="Another reviewed strategy")
    limited = await HybridRetriever(repo, vectors, provider).retrieve(
        request.model_copy(update={"limit": 1})
    )
    assert limited.truncated
    assert len(limited.results) == 1


async def test_authority_scope_type_policy_and_version_guards():
    repo, vectors, _, provider, artifact, request = await setup()
    retriever = VectorRetriever(repo, vectors, provider)
    assert not (
        await retriever.retrieve(
            request.model_copy(update={"artifact_types": (ArtifactType.KNOWLEDGE,)})
        )
    ).results
    assert not (await retriever.retrieve(request.model_copy(update={"min_confidence": 1}))).results
    foreign = artifact.model_copy(
        update={"scope": ArtifactScope(tenant_id="foreign", agent_id="alice")}
    )
    with (
        patch.object(repo, "get", return_value=foreign),
        pytest.raises(LearningScopeError, match="out-of-scope"),
    ):
        await retriever.retrieve(request)
    unreviewed = artifact.model_copy(update={"status": ArtifactStatus.CANDIDATE})
    with patch.object(repo, "get", return_value=unreviewed):
        assert not (await retriever.retrieve(request)).results
    private = artifact.model_copy(
        update={"scope": ArtifactScope(tenant_id="tenant", agent_id="bob")}
    )
    with patch.object(repo, "get", return_value=private):
        assert not (await retriever.retrieve(request)).results


async def test_projection_candidate_caps_and_graph_metadata():
    repo, vectors, graph, provider, artifact, request = await setup()
    (record,) = await vectors.get(SCOPE, provider.space, [str(artifact.artifact_id)])
    match = VectorMatch(record=record, distance=0)
    retriever = HybridRetriever(repo, vectors, provider, graph=graph)
    tiny = request.model_copy(update={"max_candidates": 1})
    with (
        patch.object(vectors, "search", return_value=[match, match]),
        pytest.raises(LearningScopeError, match="vector store exceeded"),
    ):
        await retriever.retrieve(tiny)
    other = await active_artifact(repo, SCOPE, content="Different reviewed correction")
    other_record = record.model_copy(
        update={
            "record_id": str(other.artifact_id),
            "artifact_id": other.artifact_id,
            "artifact_version": other.version,
        }
    )
    with patch.object(
        vectors, "search", return_value=[VectorMatch(record=other_record, distance=0), match]
    ):
        constrained = request.model_copy(update={"max_candidates": 2})
        # Structured seeds may occupy the whole budget, but a projection cannot grow it.
        result = await retriever.retrieve(constrained)
        assert len(result.artifacts) <= 2
    node = GraphNode(
        node_id=f"artifact:{artifact.artifact_id}",
        scope=SCOPE,
        node_type="artifact",
        properties={"artifact_id": str(artifact.artifact_id), "artifact_version": artifact.version},
    )
    graph_request = tiny.model_copy(update={"graph_depth": 1})
    for page, message in (
        (GraphPage(nodes=(node, node), relationships=()), "graph store exceeded"),
        (
            GraphPage(
                nodes=(
                    node.model_copy(
                        update={"scope": ArtifactScope(tenant_id="foreign", agent_id="alice")}
                    ),
                ),
                relationships=(),
            ),
            "out-of-scope",
        ),
        (
            GraphPage(nodes=(node.model_copy(update={"properties": {}}),), relationships=()),
            "invalid version",
        ),
    ):
        with (
            patch.object(graph, "traverse", return_value=page),
            pytest.raises(LearningScopeError, match=message),
        ):
            await retriever.retrieve(graph_request)
    stale = node.model_copy(
        update={
            "properties": {
                "artifact_id": str(artifact.artifact_id),
                "artifact_version": 1,
            }
        }
    )
    with patch.object(graph, "traverse", return_value=GraphPage(nodes=(stale,), relationships=())):
        assert (await retriever.retrieve(graph_request)).results
    # Fill the structured budget with one seed, then return a different valid graph artifact.
    new_node = node.model_copy(
        update={
            "properties": {
                "artifact_id": str(other.artifact_id),
                "artifact_version": other.version,
            }
        }
    )
    with (
        patch.object(
            vectors, "search", return_value=[VectorMatch(record=other_record, distance=0)]
        ),
        patch.object(
            graph, "traverse", return_value=GraphPage(nodes=(new_node,), relationships=())
        ),
    ):
        assert (await retriever.retrieve(graph_request)).truncated


async def test_indexer_bounds_scopes_and_invalid_evidence():
    repo, vectors, graph, provider, artifact, _ = await setup()
    index = ArtifactIndexer(repo, vectors, provider, graph=graph)
    foreign = artifact.model_copy(
        update={"scope": ArtifactScope(tenant_id="foreign", agent_id="alice")}
    )
    with (
        patch.object(repo, "get", return_value=foreign),
        pytest.raises(LearningScopeError, match="out-of-scope"),
    ):
        await index.index(SCOPE, artifact.artifact_id)
    invalid = artifact.model_copy(
        update={
            "evidence": tuple(
                evidence.model_copy(update={"valid": False}) for evidence in artifact.evidence
            )
        }
    )
    with patch.object(repo, "get", return_value=invalid):
        await index.index(SCOPE, artifact.artifact_id)
    inactive = artifact.model_copy(update={"status": ArtifactStatus.DEPRECATED})
    with patch.object(repo, "get", return_value=inactive):
        await ArtifactIndexer(repo, vectors, provider).index(SCOPE, artifact.artifact_id)
    await active_artifact(repo, SCOPE, content="Second artifact")
    with pytest.raises(ValueError, match="exceeded"):
        await index.reindex(SCOPE, max_artifacts=1)
    with (
        patch.object(
            repo,
            "query",
            return_value=ArtifactPage(artifacts=(artifact, artifact), next_cursor=None),
        ),
        pytest.raises(LearningScopeError, match="duplicate"),
    ):
        await index.reindex(SCOPE)
    with (
        patch.object(repo, "query", return_value=ArtifactPage(artifacts=(), next_cursor="bad")),
        pytest.raises(LearningScopeError, match="invalid cursor"),
    ):
        await index.reindex(SCOPE)
    pages = [
        ArtifactPage(artifacts=(artifact,), next_cursor="next"),
        ArtifactPage(artifacts=(), next_cursor=None),
    ]
    with patch.object(repo, "query", side_effect=pages):
        assert await index.reindex(SCOPE) == 1
