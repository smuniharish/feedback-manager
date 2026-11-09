"""Durable background learning and multi-agent hybrid retrieval on synthetic data.

Run with feedback-manager[lancedb,ladybugdb]. Set FEEDBACK_MANAGER_STATE_DIR to
retain state; otherwise all databases live in a temporary directory.
The explicit review below simulates a human reviewing demo data, not an
automatic publication rule for production feedback.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from sqlite_artifact_repository import SQLiteArtifactRepository
from sqlite_feedback_store import SQLiteFeedbackStore

from feedback_manager import ExecutionContext, FeedbackManager, FeedbackTarget, FeedbackTargetType
from feedback_manager.embeddings import HashingEmbeddingProvider
from feedback_manager.integrations.frameworks.custom import CustomAgentAdapter
from feedback_manager.learning import ArtifactScope, ArtifactStatus
from feedback_manager.learning.engine import LearningEngine
from feedback_manager.learning.jobs import SQLiteLearningJobStore
from feedback_manager.retrieval import ArtifactIndexer, HybridRetriever, RetrievalRequest
from feedback_manager.storage.artifacts import ArtifactQuery
from feedback_manager.storage.graph.ladybug import LadybugGraphStore
from feedback_manager.storage.vector.lancedb import LanceDBVectorStore


async def run(directory: Path) -> None:
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    scope = ArtifactScope(tenant_id="example-tenant", namespace="default", agent_id="researcher")
    repository = SQLiteArtifactRepository(directory / "artifacts.db")
    jobs = SQLiteLearningJobStore(directory / "learning-jobs.db")
    engine = LearningEngine(repository, atomic_publication=True)
    await repository.health()
    await jobs.counts()
    manager = FeedbackManager(
        store=SQLiteFeedbackStore(directory / "feedback.db"),
        learning_engine=engine,
        learning_job_store=jobs,
        require_durable_learning=True,
    )
    try:
        async with manager:
            for run_id in ("demo-run-1", "demo-run-2"):
                await manager.for_tenant(scope.tenant_id).submit(
                    source="human",
                    category="correction",
                    target=FeedbackTarget(type=FeedbackTargetType.RUN, id=run_id),
                    payload={"correction": "Validate search results before executing."},
                    execution_context=ExecutionContext(agent_id="researcher", run_id=run_id),
                    idempotency_key=run_id,
                )
            learner = manager.background_learner
            assert learner is not None
            await learner.recover()
            await learner.drain()
            page = await repository.query(ArtifactQuery(scope=scope))
            (artifact,) = page.artifacts
            if artifact.status is ArtifactStatus.CANDIDATE:
                await engine.request_review(scope, artifact.artifact_id)
            if artifact.status is not ArtifactStatus.ACTIVE:
                await engine.review(
                    scope, artifact.artifact_id, reviewer="synthetic-demo-reviewer", approved=True
                )
                artifact = await engine.activate(scope, artifact.artifact_id)

            vectors = await LanceDBVectorStore.open(str(directory / "vectors"))
            async with LadybugGraphStore(directory / "graph.db") as graph:
                provider = HashingEmbeddingProvider()
                await graph.health()
                await provider.health()
                indexer = ArtifactIndexer(repository, vectors, provider, graph=graph)
                await indexer.drain_intents(scope)
                await indexer.reindex(scope)
                retriever = HybridRetriever(repository, vectors, provider, graph=graph)
                request = RetrievalRequest(
                    scope=scope,
                    agent_id="researcher",
                    task="validate search results",
                    graph_depth=1,
                )
                result = await retriever.retrieve(request)
                assert result.artifacts == (artifact,)
                foreign = await retriever.retrieve(
                    request.model_copy(update={"agent_id": "unrelated-agent"})
                )
                assert not foreign.artifacts
                context = await CustomAgentAdapter().context_injector(retriever).build(request)
                print(context)
                print(f"reviewed artifact version: {artifact.version}")
                print(f"evidence: {artifact.evidence_count}; other-agent results: 0")
    finally:
        await manager.aclose()
        await jobs.close()
        await repository.close()


async def main() -> None:
    state = os.environ.get("FEEDBACK_MANAGER_STATE_DIR")
    if state:
        await run(Path(state))
    else:
        with tempfile.TemporaryDirectory() as directory:
            await run(Path(directory))


if __name__ == "__main__":
    asyncio.run(main())
