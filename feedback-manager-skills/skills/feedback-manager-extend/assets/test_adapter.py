"""Tests a FrameworkAdapter must pass, to copy and adapt.

Replace the imports from `adapter_template` with your adapter and the
framework's types. It needs pytest and pytest-asyncio, and runs offline:

    pytest test_adapter.py
"""

from __future__ import annotations

import pytest

from feedback_manager import (
    ExecutionContext,
    FeedbackManager,
    FeedbackStatus,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.learning import ArtifactScope
from feedback_manager.learning.engine import LearningEngine
from feedback_manager.retrieval import RetrievalRequest
from feedback_manager.storage.artifacts import ArtifactQuery, InMemoryArtifactRepository

from adapter_template import Pause, Resume, RunInfo, ToyAdapter  # Replace.

RUN = RunInfo(session_id="session-1", run_id="run-1", agent_name="planner", step="plan")


def test_the_context_carries_the_framework_identifiers() -> None:
    context = ToyAdapter().context_extractor().extract(RUN, tool_call_id="call-1")

    assert context == ExecutionContext(
        thread_id="session-1",
        run_id="run-1",
        agent_id="planner",
        node_id="plan",
        tool_call_id="call-1",
    )


def test_explicit_identifiers_override_the_framework_object() -> None:
    context = ToyAdapter().context_extractor().extract(RUN, node_id="review")

    assert context.node_id == "review"


@pytest.mark.asyncio
async def test_a_pause_is_recorded_and_resolved() -> None:
    manager = FeedbackManager(learning=False)
    bridge = ToyAdapter().human_in_the_loop(manager)
    pause = Pause(key="approve-1", question={"question": "Send the email?"})

    request = await bridge.request(
        target=FeedbackTarget(type=FeedbackTargetType.NODE, id="plan"),
        interrupt=pause,
        execution_context=ToyAdapter().context_extractor().extract(RUN),
    )
    closed = await bridge.resolve(request.feedback_id, response="yes", approved=True)

    assert request.payload == {"prompt": {"question": "Send the email?"}}
    assert request.execution_context is not None
    assert request.execution_context.interrupt_id == "approve-1"
    assert closed.status is FeedbackStatus.RESOLVED
    assert bridge.resume_command("yes", interrupt_id=pause.key) == Resume("yes", "approve-1")


@pytest.mark.asyncio
async def test_no_learned_context_renders_nothing() -> None:
    engine = LearningEngine(InMemoryArtifactRepository())
    scope = ArtifactScope(tenant_id="tenant", agent_id="planner")

    message = (
        await ToyAdapter()
        .context_injector(engine)
        .build(RetrievalRequest(scope=scope, agent_id="planner", task="plan the trip"))
    )

    assert message is None


@pytest.mark.asyncio
async def test_reviewed_lessons_render_as_delimited_reference_data() -> None:
    repository = InMemoryArtifactRepository()
    engine = LearningEngine(repository)
    scope = ArtifactScope(tenant_id="tenant", agent_id="planner")
    async with FeedbackManager(learning_engine=engine) as manager:
        for run_id in ("run-1", "run-2"):
            await manager.for_tenant("tenant").submit(
                source="human",
                category="correction",
                target=FeedbackTarget(type=FeedbackTargetType.RUN, id=run_id),
                payload={"correction": "Check opening hours before booking."},
                execution_context=ExecutionContext(agent_id="planner", run_id=run_id),
            )
        assert manager.background_learner is not None
        await manager.background_learner.drain()
    (artifact,) = (await repository.query(ArtifactQuery(scope=scope))).artifacts
    await engine.request_review(scope, artifact.artifact_id)
    await engine.review(scope, artifact.artifact_id, reviewer="reviewer", approved=True)
    await engine.activate(scope, artifact.artifact_id)

    message = (
        await ToyAdapter()
        .context_injector(engine)
        .build(RetrievalRequest(scope=scope, agent_id="planner", task="book opening hours"))
    )

    assert message is not None
    assert message["role"] == "system"
    assert "<learned_context>" in message["content"]
    assert "Check opening hours before booking." in message["content"]
