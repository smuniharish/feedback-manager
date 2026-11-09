from unittest.mock import patch
from uuid import uuid4

import pytest

from feedback_manager.embeddings import HashingEmbeddingProvider
from feedback_manager.learning import ArtifactScope, ArtifactStatus, ScopeVisibility, TenantScope
from feedback_manager.retrieval import ArtifactIndexer, HybridRetriever, RetrievalRequest
from feedback_manager.retrieval.indexing import artifact_node_id
from feedback_manager.storage.artifacts import InMemoryArtifactRepository
from feedback_manager.storage.graph import InMemoryGraphStore
from feedback_manager.storage.vector import InMemoryVectorStore
from tests.learning.test_retrieval import active_artifact, next_version

SCOPE = TenantScope(tenant_id="tenant")
AGENT = ArtifactScope(tenant_id="tenant", agent_id="alice", workflow="research")


@pytest.fixture(params=["memory", "databases"])
async def stores(request, tmp_path):
    if request.param == "memory":
        yield InMemoryVectorStore(), InMemoryGraphStore()
    else:
        pytest.importorskip("lancedb")
        pytest.importorskip("ladybug")
        from feedback_manager.storage.graph.ladybug import LadybugGraphStore
        from feedback_manager.storage.vector.lancedb import LanceDBVectorStore

        graph = LadybugGraphStore(tmp_path / "graph.db")
        try:
            yield await LanceDBVectorStore.open(str(tmp_path / "vectors")), graph
        finally:
            graph.close()


async def test_real_hybrid_end_to_end_and_reindex(stores):
    vectors, graph = stores
    repo = InMemoryArtifactRepository()
    provider = HashingEmbeddingProvider()
    artifact = await active_artifact(repo, AGENT)
    foreign = await active_artifact(repo, ArtifactScope(tenant_id="other", agent_id="alice"))
    private = await active_artifact(repo, ArtifactScope(tenant_id="tenant", agent_id="bob"))
    index = ArtifactIndexer(repo, vectors, provider, graph=graph)
    assert await index.reindex(SCOPE) == 2
    await index.index(foreign.scope, foreign.artifact_id)
    retriever = HybridRetriever(repo, vectors, provider, graph=graph)
    request = RetrievalRequest(
        scope=SCOPE,
        agent_id="alice",
        workflow="research",
        task="Search then validate results",
        graph_depth=2,
    )
    result = await retriever.retrieve(request)
    assert result.artifacts == (artifact,)
    assert "vector cosine relevance:" in result.results[0].explanation[1]
    assert result.results[0].relevance_score > 0
    assert private not in result.artifacts
    assert len(result.as_context()) <= request.max_context_chars
    assert not (
        await retriever.retrieve(request.model_copy(update={"max_context_chars": 1}))
    ).results
    newer = await repo.update(
        next_version(artifact, status=ArtifactStatus.DEPRECATED), expected_version=artifact.version
    )
    assert not (await retriever.retrieve(request)).results
    await index.index(SCOPE, artifact.artifact_id)
    assert not await vectors.get(SCOPE, provider.space, [str(artifact.artifact_id)])
    assert not await graph.get_nodes(SCOPE, [artifact_node_id(artifact.artifact_id)])
    assert newer.status is ArtifactStatus.DEPRECATED


async def test_reindex_new_space_and_rollback():
    repo, vectors = InMemoryArtifactRepository(), InMemoryVectorStore()
    artifact = await active_artifact(repo, AGENT)
    first, second = HashingEmbeddingProvider(), HashingEmbeddingProvider(revision="2")
    await ArtifactIndexer(repo, vectors, first).reindex(SCOPE)
    await ArtifactIndexer(repo, vectors, second).reindex(SCOPE)
    for provider in (second, first):
        result = await HybridRetriever(repo, vectors, provider).retrieve(
            RetrievalRequest(scope=SCOPE, agent_id="alice", workflow="research", task="validate")
        )
        assert result.artifacts == (artifact,)
    with pytest.raises(ValueError, match="positive"):
        await ArtifactIndexer(repo, vectors, first).reindex(SCOPE, max_artifacts=0)


async def test_stale_and_missing_projections_are_not_authority():
    repo, vectors = InMemoryArtifactRepository(), InMemoryVectorStore()
    provider = HashingEmbeddingProvider()
    artifact = await active_artifact(repo, AGENT)
    await ArtifactIndexer(repo, vectors, provider).index(SCOPE, artifact.artifact_id)
    (record,) = await vectors.get(SCOPE, provider.space, [str(artifact.artifact_id)])
    await vectors.upsert(record.model_copy(update={"artifact_id": uuid4(), "record_id": "missing"}))
    await vectors.upsert(record.model_copy(update={"artifact_version": artifact.version - 1}))
    result = await HybridRetriever(repo, vectors, provider).retrieve(
        RetrievalRequest(scope=SCOPE, agent_id="alice", workflow="research", task="validate")
    )
    assert len(result.results) == 1
    assert result.results[0].explanation[1] == "vector cosine relevance: 0.000"


async def test_projection_scope_violations_raise():
    from feedback_manager.learning import LearningScopeError
    from feedback_manager.storage.vector.base import VectorMatch, VectorRecord

    repo, vectors = InMemoryArtifactRepository(), InMemoryVectorStore()
    provider = HashingEmbeddingProvider()
    record = VectorRecord(
        record_id="bad",
        scope=TenantScope(tenant_id="other"),
        artifact_id=uuid4(),
        artifact_version=1,
        space=provider.space,
        vector=tuple(await provider.embed_query("validate")),
    )
    with (
        patch.object(vectors, "search", return_value=[VectorMatch(record=record, distance=0)]),
        pytest.raises(LearningScopeError, match="out-of-scope"),
    ):
        await HybridRetriever(repo, vectors, provider).retrieve(
            RetrievalRequest(scope=SCOPE, task="validate")
        )


async def test_private_candidates_do_not_starve_vector_recall(stores):
    from feedback_manager.retrieval import VectorRetriever
    from feedback_manager.storage.vector import VectorRecord

    vectors, _ = stores
    repo, provider = InMemoryArtifactRepository(), HashingEmbeddingProvider()
    artifact = await active_artifact(repo, AGENT)
    query = tuple(await provider.embed_query("validate"))
    await vectors.upsert_many(
        [
            VectorRecord(
                record_id=f"private-{i}",
                scope=SCOPE,
                artifact_id=uuid4(),
                artifact_version=1,
                space=provider.space,
                vector=query,
                metadata={"visibility": "agent", "agent_id": "bob"},
            )
            for i in range(20)
        ]
    )
    await ArtifactIndexer(repo, vectors, provider).index(SCOPE, artifact.artifact_id)
    result = await VectorRetriever(repo, vectors, provider).retrieve(
        RetrievalRequest(
            scope=SCOPE,
            agent_id="alice",
            workflow="research",
            task="validate",
            max_candidates=1,
        )
    )
    assert result.artifacts == (artifact,)


async def test_vector_agent_type_shared_scope(stores):
    from feedback_manager.retrieval import VectorRetriever

    vectors, _ = stores
    repo, provider = InMemoryArtifactRepository(), HashingEmbeddingProvider()
    artifact = await active_artifact(
        repo,
        ArtifactScope(
            tenant_id="tenant", visibility=ScopeVisibility.AGENT_TYPE, agent_type="researcher"
        ),
    )
    await ArtifactIndexer(repo, vectors, provider).index(SCOPE, artifact.artifact_id)
    assert (
        await VectorRetriever(repo, vectors, provider).retrieve(
            RetrievalRequest(
                scope=SCOPE,
                agent_type="researcher",
                task="validate",
            )
        )
    ).artifacts == (artifact,)


async def test_graph_requires_explicit_store_and_empty_query_is_supported():
    from feedback_manager.learning import UnsupportedCapabilityError

    repo, vectors = InMemoryArtifactRepository(), InMemoryVectorStore()
    retriever = HybridRetriever(repo, vectors, HashingEmbeddingProvider())
    with pytest.raises(UnsupportedCapabilityError, match="GraphStore"):
        await retriever.retrieve(RetrievalRequest(scope=SCOPE, graph_depth=1))
    assert not (await retriever.retrieve(RetrievalRequest(scope=SCOPE))).results
