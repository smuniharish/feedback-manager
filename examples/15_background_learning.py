"""Durable background learning and scoped RetrievalLoom search on synthetic data.

Set FEEDBACK_MANAGER_STATE_DIR to retain state; otherwise all databases live in
a temporary directory. RetrievalLoom's default offline indexes need no model
download or external vector/graph database.
The explicit review below simulates a human reviewing demo data, not an
automatic publication rule for production feedback.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from _assessment_workflow import assess_demo_feedback
from sqlite_artifact_repository import SQLiteArtifactRepository
from sqlite_feedback_store import SQLiteFeedbackStore

from feedback_manager import ExecutionContext, FeedbackManager, FeedbackTarget, FeedbackTargetType
from feedback_manager.integrations.frameworks.custom import CustomAgentAdapter
from feedback_manager.learning import ArtifactScope, ArtifactStatus, ContextScopeResolver
from feedback_manager.learning.assessment_verification import AssessmentVerifier
from feedback_manager.learning.engine import LearningEngine
from feedback_manager.learning.jobs import SQLiteLearningJobStore
from feedback_manager.retrieval import RetrievalRequest
from feedback_manager.storage.artifacts import ArtifactQuery
from feedback_manager.storage.assessment import SQLiteAssessmentRepository
from feedback_manager.storage.experience import SQLiteExperienceRepository


async def run(directory: Path) -> None:
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    scope = ArtifactScope(tenant_id="example-tenant", namespace="default", agent_id="researcher")
    repository = SQLiteArtifactRepository(directory / "artifacts.db")
    jobs = SQLiteLearningJobStore(directory / "learning-jobs.db")
    feedback = SQLiteFeedbackStore(directory / "feedback.db")
    experiences = SQLiteExperienceRepository(directory / "experiences.db")
    assessments = SQLiteAssessmentRepository(directory / "assessments.db")
    verifier = AssessmentVerifier(feedback, experiences, assessments)
    engine = LearningEngine(repository, atomic_publication=True, assessment_verifier=verifier)
    await repository.health()
    await jobs.counts()
    manager = FeedbackManager(
        store=feedback,
        learning_engine=engine,
        learning_job_store=jobs,
        learning_scope_resolver=ContextScopeResolver(namespace=scope.namespace),
        require_durable_learning=True,
    )
    try:
        async with manager:
            events = []
            for run_id in ("demo-run-1", "demo-run-2"):
                event = await manager.for_tenant(scope.tenant_id).submit(
                    source="human",
                    category="correction",
                    target=FeedbackTarget(type=FeedbackTargetType.RUN, id=run_id),
                    payload={"correction": "Validate search results before executing."},
                    execution_context=ExecutionContext(agent_id="researcher", run_id=run_id),
                    idempotency_key=run_id,
                )
                events.append(event)
            learner = manager.background_learner
            assert learner is not None
            for event, assessment in zip(
                events, await assess_demo_feedback(events, scope, verifier), strict=True
            ):
                await learner.enqueue_assessment(
                    event.feedback_id,
                    assessment.assessment_id,
                    assessment_version=assessment.version,
                )
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

            request = RetrievalRequest(
                scope=scope,
                agent_id="researcher",
                task="validate search results",
            )
            result = await engine.retrieve(request)
            assert result.artifacts == (artifact,)
            foreign = await engine.retrieve(
                request.model_copy(update={"agent_id": "unrelated-agent"})
            )
            assert not foreign.artifacts
            context = await CustomAgentAdapter().context_injector(engine).build(request)
            print(context)
            print(f"reviewed artifact version: {artifact.version}")
            print(f"evidence: {artifact.evidence_count}; other-agent results: 0")
    finally:
        await manager.aclose()
        await engine.aclose()
        await jobs.close()
        await repository.close()
        await experiences.close()
        await assessments.close()


async def main() -> None:
    state = os.environ.get("FEEDBACK_MANAGER_STATE_DIR")
    if state:
        await run(Path(state))
    else:
        with tempfile.TemporaryDirectory() as directory:
            await run(Path(directory))


if __name__ == "__main__":
    asyncio.run(main())
