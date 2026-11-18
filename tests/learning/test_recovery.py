import asyncio
from unittest.mock import patch
from uuid import uuid4

import pytest

from examples.sqlite_artifact_repository import SQLiteArtifactRepository
from examples.sqlite_feedback_store import SQLiteFeedbackStore
from feedback_manager import ExecutionContext, FeedbackManager, FeedbackTarget, FeedbackTargetType
from feedback_manager.assessment import (
    AssessmentEvidence,
    AssessmentStatus,
    ConservativeFeedbackCritic,
    LearningDecision,
)
from feedback_manager.experience import ExecutionExperience
from feedback_manager.learning import (
    ArtifactScope,
    ArtifactStatus,
    ContextScopeResolver,
    Evidence,
    UnsupportedCapabilityError,
)
from feedback_manager.learning.assessment_verification import AssessmentVerifier
from feedback_manager.learning.engine import LearningEngine
from feedback_manager.learning.jobs import SQLiteLearningJobStore
from feedback_manager.storage.artifacts import ArtifactQuery
from feedback_manager.storage.assessment import SQLiteAssessmentRepository
from feedback_manager.storage.experience import SQLiteExperienceRepository
from feedback_manager.storage.payload import PayloadScope
from tests.learning.test_retrieval import active_artifact, next_version

TARGET = FeedbackTarget(type=FeedbackTargetType.RUN, id="run")
SCOPE = ArtifactScope(tenant_id="tenant", agent_id="agent")


async def accepted_assessment(event, experiences, assessments):
    scope = PayloadScope.model_validate(SCOPE.model_dump())
    experience = ExecutionExperience(scope=scope, execution_id="run")
    supporting = ExecutionExperience(scope=scope, execution_id="independent")
    await experiences.create(experience)
    await experiences.create(supporting)
    evidence = AssessmentEvidence(
        evidence_id="independent",
        scope=scope,
        experience_id=supporting.experience_id,
        experience_version=supporting.version,
    )
    assessment = (
        await ConservativeFeedbackCritic().assess(event, experience, supporting=(evidence,))
    ).model_copy(update={"status": AssessmentStatus.SUPPORTED})
    await assessments.create(assessment)
    await assessments.decide(
        LearningDecision(
            scope=scope,
            assessment_id=assessment.assessment_id,
            assessment_version=assessment.version,
            accepted=True,
            reviewer="reviewer",
        )
    )
    return assessment


async def test_crash_between_feedback_commit_and_notification_recovers(tmp_path):
    feedback_path, artifact_path, jobs_path = (
        tmp_path / "feedback.db",
        tmp_path / "artifacts.db",
        tmp_path / "jobs.db",
    )
    feedback = SQLiteFeedbackStore(feedback_path)
    # Commit feedback without a learning subscriber: this is the notification gap.
    manager = FeedbackManager(store=feedback, learning=False)
    event = await manager.submit(
        source="human",
        category="correction",
        target=TARGET,
        payload={"correction": "Validate tool results"},
        execution_context=ExecutionContext(tenant_id="tenant", agent_id="agent", run_id="run"),
    )
    experience_path, assessment_path = tmp_path / "experiences.db", tmp_path / "assessments.db"
    experiences = SQLiteExperienceRepository(experience_path)
    assessments = SQLiteAssessmentRepository(assessment_path)
    assessment = await accepted_assessment(event, experiences, assessments)
    await experiences.close()
    await assessments.close()
    experiences = SQLiteExperienceRepository(experience_path)
    assessments = SQLiteAssessmentRepository(assessment_path)
    restarted_feedback = SQLiteFeedbackStore(feedback_path)
    artifacts, jobs = SQLiteArtifactRepository(artifact_path), SQLiteLearningJobStore(jobs_path)
    restarted = FeedbackManager(
        store=restarted_feedback,
        learning_engine=LearningEngine(
            artifacts,
            assessment_verifier=AssessmentVerifier(restarted_feedback, experiences, assessments),
        ),
        learning_job_store=jobs,
        learning_scope_resolver=ContextScopeResolver(namespace="default"),
        require_durable_learning=True,
    )
    learner = restarted.background_learner
    assert learner is not None
    assert await learner.recover() == 1
    assert await learner.drain() == 1
    assert not (await artifacts.query(ArtifactQuery(scope=SCOPE))).artifacts
    await learner.enqueue_assessment(
        event.feedback_id, assessment.assessment_id, assessment_version=assessment.version
    )
    await jobs.close()
    await artifacts.close()
    await experiences.close()
    await assessments.close()
    artifacts, jobs = SQLiteArtifactRepository(artifact_path), SQLiteLearningJobStore(jobs_path)
    experiences = SQLiteExperienceRepository(experience_path)
    assessments = SQLiteAssessmentRepository(assessment_path)
    restarted_feedback = SQLiteFeedbackStore(feedback_path)
    restarted = FeedbackManager(
        store=restarted_feedback,
        learning_engine=LearningEngine(
            artifacts,
            assessment_verifier=AssessmentVerifier(restarted_feedback, experiences, assessments),
        ),
        learning_job_store=jobs,
        learning_scope_resolver=ContextScopeResolver(namespace="default"),
        require_durable_learning=True,
    )
    learner = restarted.background_learner
    assert learner is not None
    assert await learner.drain() == 1
    page = await artifacts.query(ArtifactQuery(scope=SCOPE))
    assert len(page.artifacts) == 1
    learned = page.artifacts[0]
    assert learned.source_feedback_ids == (event.feedback_id,)
    assert await learner.recover() == 0
    # Rejection committed while learning is offline also recovers.
    await manager.reject(event.feedback_id, reason="withdrawn")
    await jobs.close()
    await artifacts.close()
    await experiences.close()
    await assessments.close()
    artifacts, jobs = SQLiteArtifactRepository(artifact_path), SQLiteLearningJobStore(jobs_path)
    experiences = SQLiteExperienceRepository(experience_path)
    assessments = SQLiteAssessmentRepository(assessment_path)
    restarted_feedback = SQLiteFeedbackStore(feedback_path)
    again = FeedbackManager(
        store=restarted_feedback,
        learning_engine=LearningEngine(
            artifacts,
            assessment_verifier=AssessmentVerifier(restarted_feedback, experiences, assessments),
        ),
        learning_job_store=jobs,
        learning_scope_resolver=ContextScopeResolver(namespace="default"),
        require_durable_learning=True,
    )
    assert again.background_learner is not None
    assert await jobs.checkpoint("default") == "1"
    try:
        assert await again.background_learner.recover() == 1
        assert await again.background_learner.drain() == 1
        current = await artifacts.get(SCOPE, learned.artifact_id)
        assert current.evidence_count == 0
        assert len(await artifacts.versions(SCOPE, learned.artifact_id)) == 2
        assert (await artifacts.get(SCOPE, learned.artifact_id, version=1)).evidence_count == 1
        await artifacts.health()
        assert await artifacts.get_many(SCOPE, [learned.artifact_id]) == (current,)
    finally:
        await again.aclose()
        await jobs.close()
        await artifacts.close()
        await experiences.close()
        await assessments.close()


async def test_accepted_decision_without_job_recovers_after_restart(tmp_path):
    paths = {name: tmp_path / f"{name}.db" for name in ("feedback", "artifacts", "jobs", "e", "a")}
    manager = FeedbackManager(store=SQLiteFeedbackStore(paths["feedback"]), learning=False)
    event = await manager.submit(
        source="human",
        category="correction",
        target=TARGET,
        payload={"correction": "Validate tool results"},
        execution_context=ExecutionContext(tenant_id="tenant", agent_id="agent", run_id="run"),
    )
    experiences = SQLiteExperienceRepository(paths["e"])
    assessments = SQLiteAssessmentRepository(paths["a"])
    # The decision commits, then the process stops before enqueue_assessment.
    await accepted_assessment(event, experiences, assessments)
    await experiences.close()
    await assessments.close()
    feedback = SQLiteFeedbackStore(paths["feedback"])
    experiences = SQLiteExperienceRepository(paths["e"])
    assessments = SQLiteAssessmentRepository(paths["a"])
    artifacts = SQLiteArtifactRepository(paths["artifacts"])
    jobs = SQLiteLearningJobStore(paths["jobs"])
    restarted = FeedbackManager(
        store=feedback,
        learning_engine=LearningEngine(
            artifacts, assessment_verifier=AssessmentVerifier(feedback, experiences, assessments)
        ),
        learning_job_store=jobs,
        learning_scope_resolver=ContextScopeResolver(namespace="default"),
        require_durable_learning=True,
    )
    learner = restarted.background_learner
    assert learner is not None
    try:
        assert await learner.recover_assessments(assessments) == 1
        assert await jobs.checkpoint("default:assessment-decisions") == "1"
        assert await learner.recover_assessments(assessments) == 0
        assert await learner.drain() == 1
        page = await artifacts.query(ArtifactQuery(scope=SCOPE))
        assert [item.source_feedback_ids for item in page.artifacts] == [(event.feedback_id,)]
    finally:
        await restarted.aclose()
        await jobs.close()
        await artifacts.close()
        await experiences.close()
        await assessments.close()


async def test_checkpoint_gap_replays_without_duplicate_learning(tmp_path):
    artifacts = SQLiteArtifactRepository(tmp_path / "artifacts.db")
    jobs = SQLiteLearningJobStore(tmp_path / "jobs.db")
    experiences = SQLiteExperienceRepository(tmp_path / "experiences.db")
    assessments = SQLiteAssessmentRepository(tmp_path / "assessments.db")
    feedback = SQLiteFeedbackStore(tmp_path / "feedback.db")
    manager = FeedbackManager(
        store=feedback,
        learning_engine=LearningEngine(
            artifacts, assessment_verifier=AssessmentVerifier(feedback, experiences, assessments)
        ),
        learning_job_store=jobs,
        learning_scope_resolver=ContextScopeResolver(namespace="default"),
        require_durable_learning=True,
    )
    try:
        # Do not publish: model the commit before subscriber delivery.
        from feedback_manager import FeedbackEvent, FeedbackStatus

        await manager._store.create(
            FeedbackEvent(
                source="human",
                category="correction",
                target=TARGET,
                payload={"correction": "Validate results"},
                status=FeedbackStatus.RECEIVED,
                execution_context=ExecutionContext(tenant_id="tenant", agent_id="agent"),
            )
        )
        learner = manager.background_learner
        assert learner is not None
        with (
            patch.object(jobs, "save_checkpoint", side_effect=OSError("interrupted")),
            pytest.raises(OSError, match="interrupted"),
        ):
            await learner.recover()
        assert await jobs.checkpoint("default") is None
        assert await learner.recover() == 0
        assert await learner.drain() == 1
        assert not (await artifacts.query(ArtifactQuery(scope=SCOPE))).artifacts
    finally:
        await manager.aclose()
        await jobs.close()
        await artifacts.close()
        await experiences.close()
        await assessments.close()


async def test_durable_requirements_are_not_silently_downgraded():
    with pytest.raises(UnsupportedCapabilityError, match="durable"):
        FeedbackManager(require_durable_learning=True)
    from feedback_manager.errors import FeedbackConfigurationError

    with pytest.raises(FeedbackConfigurationError, match="disabled"):
        FeedbackManager(learning=False, require_durable_learning=True)


async def test_context_entry_starts_recovery_without_new_submissions(tmp_path):
    async with FeedbackManager(store=SQLiteFeedbackStore(tmp_path / "feedback.db")) as manager:
        assert manager.background_learner is not None
        assert manager.background_learner.running


async def test_durable_artifact_reviews_pagination_and_concurrency(tmp_path):
    repository = SQLiteArtifactRepository(tmp_path / "artifacts.db")
    try:
        evidence = tuple(
            Evidence(feedback_id=uuid4(), source="human", independence_key=f"run-{number}")
            for number in range(2)
        )
        first = await active_artifact(repository, SCOPE, evidence=evidence)
        second = await active_artifact(repository, SCOPE)
        page = await repository.query(
            ArtifactQuery(scope=SCOPE, limit=1, enforce_visibility=True, agent_id="agent")
        )
        assert page.artifacts == (first,)
        assert page.next_cursor is not None
        next_page = await repository.query(ArtifactQuery(scope=SCOPE, cursor=page.next_cursor))
        assert next_page.artifacts == (second,)
        assert (
            await repository.query(
                ArtifactQuery(
                    scope=SCOPE,
                    statuses=(ArtifactStatus.ACTIVE,),
                    source_feedback_id=first.source_feedback_ids[0],
                )
            )
        ).artifacts == (first,)
        updates = await asyncio.gather(
            *(
                repository.update(
                    next_version(first, status=ArtifactStatus.DEPRECATED),
                    expected_version=first.version,
                )
                for _ in range(2)
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(item, Exception) for item in updates) == 1
    finally:
        await repository.close()
