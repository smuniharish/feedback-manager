from unittest.mock import patch
from uuid import uuid4

import pytest

from examples.sqlite_artifact_repository import SQLiteArtifactRepository
from feedback_manager.embeddings import HashingEmbeddingProvider
from feedback_manager.learning import (
    ArtifactScope,
    ArtifactStatus,
    LearningScopeError,
    LearningValidationError,
    TenantScope,
    UnsupportedCapabilityError,
)
from feedback_manager.learning.engine import LearningEngine
from feedback_manager.retrieval import ArtifactIndexer
from feedback_manager.storage.artifacts import InMemoryArtifactRepository, ProjectionIntent
from feedback_manager.storage.graph import InMemoryGraphStore
from feedback_manager.storage.vector import InMemoryVectorStore
from tests.learning.test_retrieval import active_artifact, next_version

SCOPE = ArtifactScope(tenant_id="tenant", agent_id="alice")
FOREIGN = TenantScope(tenant_id="other")


async def test_atomic_publication_crash_partial_projection_and_reopen(tmp_path):
    path = tmp_path / "artifacts.db"
    repo = SQLiteArtifactRepository(path)
    artifact = await active_artifact(repo, SCOPE)
    engine = LearningEngine(repo, atomic_publication=True)
    await engine.request_review(SCOPE, artifact.artifact_id)
    await engine.review(SCOPE, artifact.artifact_id, reviewer="reviewer", approved=True)
    artifact = await engine.activate(SCOPE, artifact.artifact_id)
    intents = await repo.pending_projections(SCOPE)
    assert len(intents) == 3
    assert [intent.artifact_version for intent in intents] == [5, 6, 7]
    await repo.acknowledge_projection(FOREIGN, intents[0].intent_id)
    assert not await repo.pending_projections(FOREIGN)
    await repo.close()

    # Publication committed, but no projection happened before restart.
    repo = SQLiteArtifactRepository(path)
    vectors, graph = InMemoryVectorStore(), InMemoryGraphStore()
    provider = HashingEmbeddingProvider()
    indexer = ArtifactIndexer(repo, vectors, provider, graph=graph)
    try:
        with (
            patch.object(graph, "upsert_relationships", side_effect=OSError("interrupted")),
            pytest.raises(OSError, match="interrupted"),
        ):
            await indexer.drain_intents(SCOPE)
        assert len(await repo.pending_projections(SCOPE)) == 3
        assert await vectors.get(SCOPE, provider.space, [str(artifact.artifact_id)])
        assert await indexer.drain_intents(SCOPE, limit=1) == 1
        assert await indexer.drain_intents(SCOPE) == 2
        assert await indexer.drain_intents(SCOPE) == 0
        (record,) = await vectors.get(SCOPE, provider.space, [str(artifact.artifact_id)])
        assert record.artifact_version == artifact.version
        await LearningEngine(repo, atomic_publication=True).invalidate_feedback(
            SCOPE, artifact.source_feedback_ids[0]
        )
        assert await indexer.drain_intents(SCOPE) == 1
        assert not await vectors.get(SCOPE, provider.space, [str(artifact.artifact_id)])
    finally:
        await repo.close()


async def test_atomic_publication_rolls_back_both_history_and_intent(tmp_path):
    repo = SQLiteArtifactRepository(tmp_path / "artifacts.db")
    try:
        artifact = await active_artifact(repo, SCOPE)
        updated = next_version(artifact, status=ArtifactStatus.DEPRECATED)
        with (
            patch.object(ProjectionIntent, "for_artifact", side_effect=OSError("interrupted")),
            pytest.raises(OSError, match="interrupted"),
        ):
            await repo.update_with_projection(updated, expected_version=artifact.version)
        assert await repo.get(SCOPE, artifact.artifact_id) == artifact
        assert len(await repo.versions(SCOPE, artifact.artifact_id)) == artifact.version
        assert not await repo.pending_projections(SCOPE)
        with pytest.raises(LearningValidationError, match="projection limit"):
            await repo.pending_projections(SCOPE, limit=0)
    finally:
        await repo.close()


async def test_explicit_capability_and_untrusted_outbox_guards():
    repo = InMemoryArtifactRepository()
    with pytest.raises(UnsupportedCapabilityError, match="atomically"):
        LearningEngine(repo, atomic_publication=True)
    artifact = await active_artifact(repo, SCOPE)
    for operation in (
        repo.update_with_projection(artifact, expected_version=artifact.version),
        repo.pending_projections(SCOPE),
        repo.acknowledge_projection(SCOPE, uuid4()),
    ):
        with pytest.raises(UnsupportedCapabilityError, match="unavailable"):
            await operation
    indexer = ArtifactIndexer(repo, InMemoryVectorStore(), HashingEmbeddingProvider())
    with pytest.raises(UnsupportedCapabilityError, match="outbox"):
        await indexer.drain_intents(SCOPE)
    with patch.object(repo, "atomic_projection_intents", True):
        with pytest.raises(ValueError, match="projection limit"):
            await indexer.drain_intents(SCOPE, limit=0)
        intent = ProjectionIntent.for_artifact(artifact).model_copy(update={"scope": FOREIGN})
        with patch.object(repo, "pending_projections", return_value=(intent, intent)):
            with pytest.raises(LearningScopeError, match="bound"):
                await indexer.drain_intents(SCOPE, limit=1)
            with pytest.raises(LearningScopeError, match="foreign"):
                await indexer.drain_intents(SCOPE)
