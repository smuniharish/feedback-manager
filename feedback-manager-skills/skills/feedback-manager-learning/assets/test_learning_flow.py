"""A pytest template for testing an application's learning integration.

Copy this file into your test suite and replace the manager construction marked
"Replace" with your own stores. It needs pytest and pytest-asyncio, and runs
offline:

    pytest test_learning_flow.py
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from feedback_manager import ExecutionContext, FeedbackManager, FeedbackTarget, FeedbackTargetType
from feedback_manager.integrations.frameworks.custom import CustomAgentAdapter
from feedback_manager.learning import ArtifactScope, ArtifactStatus, LearningValidationError
from feedback_manager.learning.engine import LearningEngine
from feedback_manager.retrieval import RetrievalRequest
from feedback_manager.storage.artifacts import ArtifactQuery, InMemoryArtifactRepository

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from feedback_manager.learning import LearnedArtifact

pytestmark = pytest.mark.asyncio

TENANT = "test-tenant"
AGENT = "test-agent"
LESSON = "Validate tool results before acting."
SCOPE = ArtifactScope(tenant_id=TENANT, agent_id=AGENT)


@pytest.fixture
def repository() -> InMemoryArtifactRepository:
    return InMemoryArtifactRepository()  # Replace with your artifact repository.


@pytest.fixture
def engine(repository: InMemoryArtifactRepository) -> LearningEngine:
    return LearningEngine(repository)


@pytest.fixture
async def manager(engine: LearningEngine) -> AsyncIterator[FeedbackManager]:
    async with FeedbackManager(learning_engine=engine) as manager:  # Replace: your store.
        yield manager


async def correct(manager: FeedbackManager, *run_ids: str) -> None:
    for run_id in run_ids:
        await manager.for_tenant(TENANT).submit(
            source="human",
            category="correction",
            target=FeedbackTarget(type=FeedbackTargetType.RUN, id=run_id),
            payload={"correction": LESSON},
            execution_context=ExecutionContext(agent_id=AGENT, run_id=run_id),
            idempotency_key=f"correction:{run_id}",
        )
    assert manager.background_learner is not None
    await manager.background_learner.drain()


async def only_artifact(repository: InMemoryArtifactRepository) -> LearnedArtifact:
    (artifact,) = (await repository.query(ArtifactQuery(scope=SCOPE))).artifacts
    return artifact


async def publish(engine: LearningEngine, artifact: LearnedArtifact) -> LearnedArtifact:
    await engine.request_review(SCOPE, artifact.artifact_id)
    await engine.review(SCOPE, artifact.artifact_id, reviewer="test-reviewer", approved=True)
    return await engine.activate(SCOPE, artifact.artifact_id)


def request(agent_id: str = AGENT, tenant_id: str = TENANT) -> RetrievalRequest:
    scope = ArtifactScope(tenant_id=tenant_id, agent_id=agent_id)
    return RetrievalRequest(scope=scope, agent_id=agent_id, task="validate tool results")


async def test_feedback_becomes_a_candidate_that_is_not_retrieved(
    manager: FeedbackManager, engine: LearningEngine, repository: InMemoryArtifactRepository
) -> None:
    await correct(manager, "run-1", "run-2")

    artifact = await only_artifact(repository)
    assert artifact.status is ArtifactStatus.CANDIDATE
    assert artifact.evidence_count == 2
    assert not (await engine.retrieve(request())).results


async def test_one_execution_is_not_enough_evidence_to_publish(
    manager: FeedbackManager, engine: LearningEngine, repository: InMemoryArtifactRepository
) -> None:
    await correct(manager, "run-1")
    artifact = await only_artifact(repository)
    await engine.request_review(SCOPE, artifact.artifact_id)

    with pytest.raises(LearningValidationError):
        await engine.review(SCOPE, artifact.artifact_id, reviewer="test-reviewer", approved=True)


async def test_a_reviewed_lesson_reaches_the_agent(
    manager: FeedbackManager, engine: LearningEngine, repository: InMemoryArtifactRepository
) -> None:
    await correct(manager, "run-1", "run-2")
    artifact = await publish(engine, await only_artifact(repository))

    context = await CustomAgentAdapter().context_injector(engine).build(request())
    assert artifact.status is ArtifactStatus.ACTIVE
    assert context is not None
    assert LESSON in context


async def test_lessons_stay_with_their_tenant_and_agent(
    manager: FeedbackManager, engine: LearningEngine, repository: InMemoryArtifactRepository
) -> None:
    await correct(manager, "run-1", "run-2")
    await publish(engine, await only_artifact(repository))

    assert not (await engine.retrieve(request(agent_id="another-agent"))).results
    assert not (await engine.retrieve(request(tenant_id="another-tenant"))).results


async def test_retried_feedback_adds_no_evidence(
    manager: FeedbackManager, repository: InMemoryArtifactRepository
) -> None:
    await correct(manager, "run-1", "run-2")
    await correct(manager, "run-1", "run-2")

    assert (await only_artifact(repository)).evidence_count == 2


async def test_invalidated_feedback_sends_the_lesson_back_to_review(
    manager: FeedbackManager, engine: LearningEngine, repository: InMemoryArtifactRepository
) -> None:
    await correct(manager, "run-1", "run-2")
    artifact = await publish(engine, await only_artifact(repository))
    feedback_id = artifact.evidence[0].feedback_id
    assert feedback_id is not None

    (changed,) = await engine.invalidate_feedback(SCOPE, feedback_id)
    assert changed.status is ArtifactStatus.UNDER_REVIEW
    assert not (await engine.retrieve(request())).results
