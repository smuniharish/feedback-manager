"""Turn two explicitly scoped corrections into a reviewed learning artifact."""

import asyncio

from _assessment_workflow import assess_demo_feedback

from feedback_manager import ExecutionContext, FeedbackManager, FeedbackTarget, FeedbackTargetType
from feedback_manager.learning import ArtifactScope
from feedback_manager.learning.assessment_verification import AssessmentVerifier
from feedback_manager.learning.engine import LearningEngine
from feedback_manager.learning.signals import LearningSignalExtractor
from feedback_manager.retrieval import RetrievalRequest
from feedback_manager.storage import InMemoryFeedbackStore
from feedback_manager.storage.artifacts import InMemoryArtifactRepository
from feedback_manager.storage.assessment import InMemoryAssessmentRepository
from feedback_manager.storage.experience import InMemoryExperienceRepository


async def main() -> None:
    store = InMemoryFeedbackStore()
    manager = FeedbackManager(store=store, learning=False)
    scope = ArtifactScope(tenant_id="example-tenant", agent_id="research-agent")
    extractor = LearningSignalExtractor()
    verifier = AssessmentVerifier(
        store, InMemoryExperienceRepository(), InMemoryAssessmentRepository()
    )
    engine = LearningEngine(InMemoryArtifactRepository(), assessment_verifier=verifier)
    signals = []
    events = []
    for run in ("run-1", "run-2"):
        event = await manager.submit(
            source="human",
            category="comment",
            target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id=run),
            payload={"improvement": "Search, validate results, then execute."},
            execution_context=ExecutionContext(tenant_id=scope.tenant_id, run_id=run),
        )
        events.append(event)
    assessments = await assess_demo_feedback(events, scope, verifier)
    for event, assessment in zip(events, assessments, strict=True):
        extracted = extractor.extract(event, scope=scope)
        if extracted.signal is None:
            raise RuntimeError(f"Feedback could not be learned: {extracted.reason}")
        evidence = extracted.signal.evidence.model_copy(
            update={
                "assessment_id": assessment.assessment_id,
                "assessment_version": assessment.version,
            }
        )
        signals.append(extracted.signal.model_copy(update={"evidence": evidence}))
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
