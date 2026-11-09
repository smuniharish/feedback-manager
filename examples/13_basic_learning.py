"""Turn two explicitly scoped corrections into a reviewed learning artifact."""

import asyncio

from feedback_manager import ExecutionContext, FeedbackManager, FeedbackTarget, FeedbackTargetType
from feedback_manager.learning import ArtifactScope
from feedback_manager.learning.engine import LearningEngine
from feedback_manager.learning.signals import LearningSignalExtractor
from feedback_manager.retrieval import RetrievalRequest
from feedback_manager.storage.artifacts import InMemoryArtifactRepository


async def main() -> None:
    manager = FeedbackManager(learning=False)
    scope = ArtifactScope(tenant_id="example-tenant", agent_id="research-agent")
    extractor = LearningSignalExtractor()
    engine = LearningEngine(InMemoryArtifactRepository())
    signals = []
    for run in ("run-1", "run-2"):
        event = await manager.submit(
            source="human",
            category="comment",
            target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id=run),
            payload={"improvement": "Search, validate results, then execute."},
            execution_context=ExecutionContext(tenant_id=scope.tenant_id, run_id=run),
        )
        extracted = extractor.extract(event, scope=scope)
        if extracted.signal is None:
            raise RuntimeError(f"Feedback could not be learned: {extracted.reason}")
        signals.append(extracted.signal)
    candidate = (await engine.process(signals)).artifacts[0]
    print(f"Candidate: {candidate.status}, evidence={candidate.evidence_count}")
    await engine.request_review(scope, candidate.artifact_id)
    await engine.review(scope, candidate.artifact_id, reviewer="example-reviewer", approved=True)
    await engine.activate(scope, candidate.artifact_id)
    result = await engine.retrieve(
        RetrievalRequest(scope=scope, agent_id="research-agent", task="validate results")
    )
    print(f"Retrieved: {result.as_context()}")


if __name__ == "__main__":
    asyncio.run(main())
