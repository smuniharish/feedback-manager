import asyncio
from unittest.mock import patch

import pytest

from examples.sqlite_artifact_repository import SQLiteArtifactRepository
from examples.sqlite_feedback_store import SQLiteFeedbackStore
from feedback_manager import ExecutionContext, FeedbackManager, FeedbackTarget, FeedbackTargetType
from feedback_manager.learning import ArtifactScope, ArtifactStatus, UnsupportedCapabilityError
from feedback_manager.learning.engine import LearningEngine
from feedback_manager.learning.jobs import SQLiteLearningJobStore
from feedback_manager.storage.artifacts import ArtifactQuery
from tests.learning.test_retrieval import active_artifact, next_version

TARGET = FeedbackTarget(type=FeedbackTargetType.RUN, id="run")
SCOPE = ArtifactScope(tenant_id="tenant", agent_id="agent")


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
    artifacts, jobs = SQLiteArtifactRepository(artifact_path), SQLiteLearningJobStore(jobs_path)
    restarted = FeedbackManager(
        store=SQLiteFeedbackStore(feedback_path),
        learning_engine=LearningEngine(artifacts),
        learning_job_store=jobs,
        require_durable_learning=True,
    )
    learner = restarted.background_learner
    assert learner is not None
    assert await learner.recover() == 1
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
    artifacts, jobs = SQLiteArtifactRepository(artifact_path), SQLiteLearningJobStore(jobs_path)
    again = FeedbackManager(
        store=SQLiteFeedbackStore(feedback_path),
        learning_engine=LearningEngine(artifacts),
        learning_job_store=jobs,
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


async def test_checkpoint_gap_replays_without_duplicate_learning(tmp_path):
    artifacts = SQLiteArtifactRepository(tmp_path / "artifacts.db")
    jobs = SQLiteLearningJobStore(tmp_path / "jobs.db")
    manager = FeedbackManager(
        store=SQLiteFeedbackStore(tmp_path / "feedback.db"),
        learning_engine=LearningEngine(artifacts),
        learning_job_store=jobs,
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
        assert len((await artifacts.query(ArtifactQuery(scope=SCOPE))).artifacts) == 1
    finally:
        await manager.aclose()
        await jobs.close()
        await artifacts.close()


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
        first = await active_artifact(repository, SCOPE)
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
