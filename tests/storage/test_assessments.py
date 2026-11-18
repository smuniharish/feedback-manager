import asyncio
import sqlite3
import threading
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from feedback_manager.assessment import (
    AssessmentEvidence,
    AssessmentStatus,
    ConservativeFeedbackCritic,
    LearningDecision,
)
from feedback_manager.learning.errors import LearningValidationError
from feedback_manager.storage.assessment import (
    AssessmentConflictError,
    AssessmentNotFoundError,
    AssessmentStoreError,
    InMemoryAssessmentRepository,
    SQLiteAssessmentRepository,
)
from feedback_manager.storage.payload import PayloadScope
from tests.learning.test_assessment import SCOPE, sources


async def test_concurrent_assessment_updates_are_fenced() -> None:
    feedback, experience = sources()
    initial = await ConservativeFeedbackCritic().assess(feedback, experience)
    repository = InMemoryAssessmentRepository()
    await repository.create(initial)
    with pytest.raises(TypeError, match="LearningDecision"):
        await repository.decide(object())  # type: ignore[arg-type]
    updated = initial.model_copy(
        update={"version": 2, "assessed_at": initial.assessed_at + timedelta(seconds=1)}
    )
    results = await asyncio.gather(
        repository.update(updated, expected_version=1),
        repository.update(updated, expected_version=1),
        return_exceptions=True,
    )
    assert sum(isinstance(result, AssessmentConflictError) for result in results) == 1
    assert await repository.versions(SCOPE, initial.assessment_id) == (initial, updated)


async def test_invalid_assessment_updates_and_queries_fail_explicitly() -> None:
    feedback, experience = sources()
    initial = await ConservativeFeedbackCritic().assess(feedback, experience)
    repository = InMemoryAssessmentRepository()
    with pytest.raises(AssessmentConflictError, match="version 1"):
        await repository.create(initial.model_copy(update={"version": 2}))
    await repository.create(initial)
    with pytest.raises(TypeError, match="FeedbackAssessment"):
        await repository.create(object())  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        await repository.create(initial.model_copy(update={"feedback_digest": "bad"}))
    changed = {"version": 2, "assessed_at": initial.assessed_at + timedelta(seconds=1)}
    for fields, error in (
        ({"version": 3}, "increase"),
        ({"assessed_at": initial.assessed_at}, "time"),
        ({"feedback_id": uuid4()}, "identity"),
        ({"experience_id": uuid4()}, "identity"),
    ):
        with pytest.raises(AssessmentConflictError, match=error):
            await repository.update(
                initial.model_copy(update={**changed, **fields}), expected_version=1
            )
    with pytest.raises(AssessmentNotFoundError):
        await repository.update(
            initial.model_copy(update={**changed, "assessment_id": uuid4()}), expected_version=1
        )
    with pytest.raises(AssessmentNotFoundError):
        await repository.get(SCOPE, initial.assessment_id, version=2)
    with pytest.raises(AssessmentNotFoundError):
        await repository.versions(SCOPE, uuid4())
    with pytest.raises(LearningValidationError, match="after_version"):
        await repository.versions(SCOPE, initial.assessment_id, after_version=-1)
    for invalid in (0, True):
        with pytest.raises(LearningValidationError, match="positive"):
            await repository.get(SCOPE, initial.assessment_id, version=invalid)
        with pytest.raises(LearningValidationError, match="positive"):
            await repository.update(initial, expected_version=invalid)
        with pytest.raises(LearningValidationError, match="positive"):
            await repository.for_feedback(SCOPE, feedback.feedback_id, limit=invalid)
    with pytest.raises(LearningValidationError, match="1000"):
        await repository.for_feedback(SCOPE, feedback.feedback_id, limit=1001)
    with pytest.raises(LearningValidationError, match="cursor"):
        await repository.for_feedback(SCOPE, feedback.feedback_id, cursor="bad")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="UUID"):
        await repository.get(SCOPE, "bad")  # type: ignore[arg-type]


async def test_sqlite_restart_concurrency_and_rollback(tmp_path: Path) -> None:
    path = tmp_path / "assessments.db"
    feedback, experience = sources()
    initial = await ConservativeFeedbackCritic().assess(feedback, experience)
    first = await SQLiteAssessmentRepository.open(path)
    second = await SQLiteAssessmentRepository.open(path)
    updated = initial.model_copy(
        update={"version": 2, "assessed_at": initial.assessed_at + timedelta(seconds=1)}
    )
    try:
        await first.create(initial)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "CREATE TRIGGER fail_version BEFORE INSERT ON assessment_versions "
                "WHEN NEW.version=2 BEGIN SELECT RAISE(ABORT, 'injected'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            await first.update(updated, expected_version=1)
        assert await second.get(SCOPE, initial.assessment_id) == initial
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("DROP TRIGGER fail_version")
        results = await asyncio.gather(
            first.update(updated, expected_version=1),
            second.update(updated, expected_version=1),
            return_exceptions=True,
        )
        assert sum(isinstance(result, AssessmentConflictError) for result in results) == 1
        assert sum(result == updated for result in results) == 1
    finally:
        await first.close()
        await second.close()
    restarted = await SQLiteAssessmentRepository.open(path)
    try:
        assert restarted.durable
        assert await restarted.get(SCOPE, initial.assessment_id) == updated
        assert await restarted.versions(SCOPE, initial.assessment_id) == (initial, updated)
        assert await restarted.versions(SCOPE, initial.assessment_id, after_version=10**100) == ()
        assert await restarted.create(initial) == updated
    finally:
        await restarted.close()
        await restarted.close()
    with pytest.raises(AssessmentStoreError, match="closed"):
        await restarted.health()


async def test_sqlite_decisions_are_atomic_and_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "decisions.db"
    feedback, experience = sources()
    initial = (await ConservativeFeedbackCritic().assess(feedback, experience)).model_copy(
        update={
            "status": AssessmentStatus.SUPPORTED,
            "supporting_evidence": (
                AssessmentEvidence(
                    evidence_id="independent",
                    scope=SCOPE,
                    experience_id=uuid4(),
                    experience_version=1,
                ),
            ),
        }
    )
    first = await SQLiteAssessmentRepository.open(path)
    second = await SQLiteAssessmentRepository.open(path)
    decision = LearningDecision(
        scope=SCOPE,
        assessment_id=initial.assessment_id,
        assessment_version=1,
        accepted=True,
        reviewer="reviewer",
    )
    try:
        await first.create(initial)
        results = await asyncio.gather(
            first.decide(decision),
            second.decide(decision.model_copy(update={"decision_id": uuid4(), "accepted": False})),
            return_exceptions=True,
        )
        assert sum(isinstance(result, AssessmentConflictError) for result in results) == 1
        winner = next(result for result in results if isinstance(result, LearningDecision))
    finally:
        await first.close()
        await second.close()
    reopened = await SQLiteAssessmentRepository.open(path)
    try:
        assert await reopened.decision(SCOPE, initial.assessment_id, assessment_version=1) == winner
        foreign = PayloadScope(tenant_id="other", namespace="app", agent_id="agent")
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "UPDATE assessment_decisions SET document=?",
                (winner.model_copy(update={"scope": foreign}).model_dump_json(),),
            )
        with pytest.raises(AssessmentStoreError, match="identity"):
            await reopened.decision(SCOPE, initial.assessment_id, assessment_version=1)
    finally:
        await reopened.close()


async def test_decision_validation_rejects_bad_time_and_reused_identity() -> None:
    feedback, experience = sources()
    initial = (await ConservativeFeedbackCritic().assess(feedback, experience)).model_copy(
        update={
            "status": AssessmentStatus.SUPPORTED,
            "supporting_evidence": (
                AssessmentEvidence(
                    evidence_id="independent",
                    scope=SCOPE,
                    experience_id=uuid4(),
                    experience_version=1,
                ),
            ),
        }
    )
    repository = InMemoryAssessmentRepository()
    await repository.create(initial)
    decision = LearningDecision(
        scope=SCOPE,
        assessment_id=initial.assessment_id,
        assessment_version=1,
        accepted=True,
        reviewer="reviewer",
    )
    with pytest.raises(AssessmentConflictError, match="time"):
        await repository.decide(
            decision.model_copy(update={"decided_at": initial.assessed_at - timedelta(seconds=1)})
        )
    unsupported = initial.model_copy(update={"assessment_id": uuid4(), "supporting_evidence": ()})
    await repository.create(unsupported)
    with pytest.raises(AssessmentConflictError, match="independent"):
        await repository.decide(
            decision.model_copy(update={"assessment_id": unsupported.assessment_id})
        )
    await repository.decide(decision)
    next_version = initial.model_copy(
        update={"version": 2, "assessed_at": initial.assessed_at + timedelta(seconds=1)}
    )
    await repository.update(next_version, expected_version=1)
    with pytest.raises(AssessmentConflictError, match="ID already exists"):
        await repository.decide(
            decision.model_copy(
                update={
                    "assessment_version": 2,
                    "decided_at": next_version.assessed_at + timedelta(seconds=1),
                }
            )
        )


async def test_sqlite_rejects_corrupted_scope(tmp_path: Path) -> None:
    path = tmp_path / "assessments.db"
    feedback, experience = sources()
    assessment = await ConservativeFeedbackCritic().assess(feedback, experience)
    repository = await SQLiteAssessmentRepository.open(path)
    foreign = assessment.model_copy(
        update={"scope": PayloadScope(tenant_id="other", namespace="app", agent_id="agent")}
    )
    try:
        await repository.create(assessment)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "UPDATE assessment_records SET document=?", (foreign.model_dump_json(),)
            )
        with pytest.raises(AssessmentStoreError, match="identity"):
            await repository.get(SCOPE, assessment.assessment_id)
        with pytest.raises(AssessmentStoreError, match="identity"):
            await repository.for_feedback(SCOPE, assessment.feedback_id)
    finally:
        await repository.close()


async def test_cancelled_sqlite_open_closes_constructed_connection(tmp_path: Path) -> None:
    entered, release = threading.Event(), threading.Event()
    created: list[SQLiteAssessmentRepository] = []

    class DelayedRepository(SQLiteAssessmentRepository):
        def __init__(self, path: str | Path) -> None:
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("open did not resume")
            super().__init__(path)
            created.append(self)

    pending = asyncio.create_task(DelayedRepository.open(tmp_path / "assessments.db"))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait), timeout=2)
        pending.cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert len(created) == 1
    with pytest.raises(AssessmentStoreError, match="closed"):
        await created[0].health()


@pytest.mark.parametrize("filename", ["", ":memory:", "file:archive?mode=memory"])
async def test_sqlite_requires_durable_file(filename: str) -> None:
    with pytest.raises(LearningValidationError, match="local database file"):
        await SQLiteAssessmentRepository.open(filename)
