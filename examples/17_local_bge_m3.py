"""Use local BGE-M3 dense embeddings with RetrievalLoom, without network access."""

from __future__ import annotations

import asyncio
from pathlib import Path

from _bge_m3_embedding import LocalBgeM3Embedding
from resourcera import ResourceConfig
from retrievalloom import (
    ChannelsConfig,
    Components,
    FuzzyConfig,
    IndexChannelConfig,
    LifecycleConfig,
    MethodConfig,
    OffloaderConfig,
    RerankingConfig,
    RetrievalConfig,
    TenantPolicy,
    create_retrieval_engine,
)

from feedback_manager.learning.engine import LearningEngine
from feedback_manager.learning.models import ArtifactScope, ExecutionOutcome
from feedback_manager.learning.signals import LearningSignalExtractor
from feedback_manager.retrieval import RetrievalLoomRetriever, RetrievalRequest
from feedback_manager.storage.artifacts import InMemoryArtifactRepository

MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "bge-m3"


async def main() -> None:
    embedding = LocalBgeM3Embedding(MODEL_PATH)
    repository = InMemoryArtifactRepository()
    retrieval_engine = create_retrieval_engine(
        lifecycle=LifecycleConfig(background=False),
        retrieval=RetrievalConfig(
            channels=ChannelsConfig(
                index=IndexChannelConfig(
                    keyword=MethodConfig(enabled=False),
                    fuzzy=FuzzyConfig(enabled=False),
                    sparse=MethodConfig(enabled=False),
                    dense=MethodConfig(enabled=True),
                )
            ),
            reranking=RerankingConfig(enabled=False),
            default_tenant_policy=TenantPolicy(max_top_k=1000, max_candidate_k=1000),
        ),
        components=Components(embedding=embedding),
        offloader=OffloaderConfig(
            resource_config=ResourceConfig(
                cpu_budget_fraction=1.0,
                managed_state_budget_bytes=256 * 1024 * 1024,
            )
        ),
    )
    retriever = RetrievalLoomRetriever(repository, engine=retrieval_engine)
    learner = LearningEngine(repository, retriever=retriever)
    scope = ArtifactScope(
        tenant_id="example-tenant",
        namespace="default",
        agent_id="research-agent",
    )
    extractor = LearningSignalExtractor()
    signals = [
        extractor.extract_outcome(
            ExecutionOutcome(
                execution_id=run_id,
                scope=scope,
                success=True,
                description="Verify source facts before summarizing.",
            )
        )
        for run_id in ("run-1", "run-2")
    ]

    try:
        candidate = (await learner.process(signals)).artifacts[0]
        await learner.request_review(scope, candidate.artifact_id)
        await learner.review(
            scope, candidate.artifact_id, reviewer="example-reviewer", approved=True
        )
        await learner.activate(scope, candidate.artifact_id)
        result = await learner.retrieve(
            RetrievalRequest(
                scope=scope,
                agent_id=scope.agent_id,
                task="Check accuracy before writing a summary.",
            )
        )
        if not result.artifacts:
            raise RuntimeError("The dense embedding search did not return the demo artifact")
        print(f"Retrieved with local BGE-M3: {result.artifacts[0].title}")
    finally:
        await learner.aclose()
        await retrieval_engine.close()
        await embedding.close()


if __name__ == "__main__":
    asyncio.run(main())
