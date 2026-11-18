import asyncio
import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

import feedback_manager.storage.experience as experience_storage
from feedback_manager.experience import (
    ExecutionExperience,
    ObservableDecision,
    ObservableToolInteraction,
)
from feedback_manager.learning.errors import LearningValidationError
from feedback_manager.storage import InMemoryPayloadStore, PayloadScope
from feedback_manager.storage.experience import (
    ExperienceConflictError,
    ExperienceNotFoundError,
    InMemoryExperienceRepository,
)

SCOPE = PayloadScope(tenant_id="tenant", namespace="app", agent_id="agent")
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def experience() -> ExecutionExperience:
    return ExecutionExperience(scope=SCOPE, execution_id="run", created_at=NOW, updated_at=NOW)


async def test_experience_preserves_structured_payloads_and_observable_steps() -> None:
    payloads = InMemoryPayloadStore()
    original = b'{"rows":[{"value":1}],"text":"original"}'
    payload = await payloads.write(
        SCOPE, original, content_type="application/json", serialization_version="1"
    )
    tool = ObservableToolInteraction(tool_name="lookup", input=payload, output=payload)
    decision = ObservableDecision(
        execution_id="run",
        sequence=0,
        decision_type="select_tool",
        context=payload,
        selected_action=payload,
        tool_interaction_ids=(tool.interaction_id,),
    )
    episode = ExecutionExperience(
        scope=SCOPE,
        execution_id="run",
        original_input=payload,
        final_output=payload,
        decisions=(decision,),
        tool_interactions=(tool,),
    )
    round_trip = ExecutionExperience.model_validate_json(episode.model_dump_json())
    assert round_trip == episode
    assert round_trip.original_input is not None
    assert await payloads.read(round_trip.scope, round_trip.original_input.payload_id) == original
    assert round_trip.decisions[0].tool_interaction_ids == (tool.interaction_id,)
    with pytest.raises(ValidationError):
        episode.execution_id = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        episode.decisions[0].sequence = 2  # type: ignore[misc]
    invalid_scope = SCOPE.model_copy(update={"namespace": ""})
    forged_payload = payload.model_copy(update={"scope": invalid_scope})
    with pytest.raises(ValidationError):
        ExecutionExperience(scope=SCOPE, execution_id="run", original_input=forged_payload)
    with pytest.raises(ValidationError):
        await InMemoryExperienceRepository().create(
            episode.model_copy(update={"original_input": forged_payload})
        )


async def test_experience_rejects_foreign_payload_references() -> None:
    payloads = InMemoryPayloadStore()
    foreign = PayloadScope(tenant_id="tenant", namespace="other", agent_id="agent")
    reference = await payloads.write(
        foreign, b"private", content_type="text/plain", serialization_version="1"
    )
    decision = ObservableDecision(
        execution_id="run", sequence=0, decision_type="action", context=reference
    )
    tool = ObservableToolInteraction(tool_name="lookup", output=reference)
    for fields in (
        {"original_input": reference},
        {"decisions": (decision,)},
        {"tool_interactions": (tool,)},
    ):
        with pytest.raises(ValidationError, match="payload scope"):
            ExecutionExperience.model_validate({"scope": SCOPE, "execution_id": "run", **fields})


def test_experience_rejects_inconsistent_observations_and_timestamps() -> None:
    first = ObservableDecision(execution_id="run", sequence=0, decision_type="action")
    other_run = ObservableDecision(execution_id="other", sequence=1, decision_type="action")
    tool = ObservableToolInteraction(tool_name="lookup")
    for changes, error in (
        ({"updated_at": NOW - timedelta(seconds=1)}, "updated_at"),
        ({"started_at": NOW, "completed_at": NOW - timedelta(seconds=1)}, "completion"),
        ({"decisions": (other_run,)}, "execution_id"),
        ({"decisions": (first, first)}, "decision IDs"),
        ({"decisions": (first, first.model_copy(update={"decision_id": uuid4()}))}, "sequences"),
        ({"tool_interactions": (tool, tool)}, "tool interaction IDs"),
    ):
        data = experience().model_dump()
        data.update(changes)
        with pytest.raises(ValidationError, match=error):
            ExecutionExperience.model_validate(data)
    with pytest.raises(ValidationError, match="tool completion"):
        ObservableToolInteraction(
            tool_name="lookup", started_at=NOW, completed_at=NOW - timedelta(seconds=1)
        )


async def test_concurrent_updates_are_fenced_and_history_is_preserved() -> None:
    repository = InMemoryExperienceRepository()
    first = await repository.create(experience())
    second = first.model_copy(
        update={"version": 2, "updated_at": NOW + timedelta(seconds=1), "status": "completed"}
    )
    barrier = threading.Barrier(2)

    def update() -> ExecutionExperience:
        barrier.wait()
        return asyncio.run(repository.update(second, expected_version=1))

    results = await asyncio.gather(
        asyncio.to_thread(update),
        asyncio.to_thread(update),
        return_exceptions=True,
    )
    assert sum(isinstance(item, ExperienceConflictError) for item in results) == 1
    assert await repository.versions(SCOPE, first.experience_id) == (first, second)
    assert await repository.get(SCOPE, first.experience_id, version=1) == first


async def test_archive_work_does_not_block_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = threading.Event()
    release = threading.Event()
    original_copy = experience_storage._copy

    def blocked_copy(value: ExecutionExperience) -> ExecutionExperience:
        entered.set()
        if not release.wait(timeout=3):
            raise RuntimeError("archive copy blocked the event loop")
        return original_copy(value)

    monkeypatch.setattr(experience_storage, "_copy", blocked_copy)
    pending = asyncio.create_task(InMemoryExperienceRepository().create(experience()))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait), timeout=1)
        assert not pending.done()
    finally:
        release.set()
        await pending


async def test_repository_rejects_conflicts_invalid_versions_and_unknown_cursors() -> None:
    repository = InMemoryExperienceRepository()
    first = await repository.create(experience())
    with pytest.raises(ExperienceConflictError, match="version 1"):
        await repository.create(first.model_copy(update={"version": 2, "experience_id": uuid4()}))
    with pytest.raises(ExperienceConflictError, match="already exists"):
        await repository.create(first.model_copy(update={"status": "different"}))
    changes = (
        ({"version": 3, "updated_at": NOW + timedelta(seconds=1)}, "increase"),
        ({"version": 2}, "timestamps"),
        (
            {"version": 2, "updated_at": NOW + timedelta(seconds=1), "execution_id": "other"},
            "identity",
        ),
    )
    for update, error in changes:
        with pytest.raises(ExperienceConflictError, match=error):
            await repository.update(first.model_copy(update=update), expected_version=1)
    with pytest.raises(ExperienceNotFoundError):
        await repository.update(
            first.model_copy(update={"experience_id": uuid4()}), expected_version=1
        )
    for bad in (0, True):
        with pytest.raises(LearningValidationError, match="positive integer"):
            await repository.get(SCOPE, first.experience_id, version=bad)
        with pytest.raises(LearningValidationError, match="positive integer"):
            await repository.update(first, expected_version=bad)
        with pytest.raises(LearningValidationError, match="positive integer"):
            await repository.query(SCOPE, limit=bad)
    with pytest.raises(ExperienceNotFoundError):
        await repository.get(SCOPE, first.experience_id, version=2)
    with pytest.raises(LearningValidationError, match="after_version"):
        await repository.versions(SCOPE, first.experience_id, after_version=-1)
    assert await repository.versions(SCOPE, first.experience_id, after_version=1) == ()
    with pytest.raises(LearningValidationError, match="1000"):
        await repository.query(SCOPE, limit=1001)
    with pytest.raises(LearningValidationError, match="cursor"):
        await repository.query(SCOPE, cursor=uuid4())
    with pytest.raises(LearningValidationError, match="cursor"):
        await repository.query(SCOPE, cursor="bad")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="UUID"):
        await repository.get(SCOPE, "bad")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ExecutionExperience"):
        await repository.create(object())  # type: ignore[arg-type]
