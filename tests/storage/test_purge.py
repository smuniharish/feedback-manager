from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest

from feedback_manager.assessment import AssessmentStatus, LearningDecision
from feedback_manager.experience import ExecutionExperience
from feedback_manager.storage.artifacts import ArtifactQuery
from feedback_manager.storage.assessment import AssessmentNotFoundError
from feedback_manager.storage.experience import ExperienceNotFoundError
from feedback_manager.storage.payload import (
    InMemoryPayloadStore,
    PayloadNotFoundError,
    PayloadScope,
)
from feedback_manager.storage.purge import ExperiencePurger
from tests.learning.test_recovery import accepted_assessment
from tests.learning.test_worker import SCOPE, submit, worker

PAYLOAD_SCOPE = PayloadScope.model_validate(SCOPE.model_dump())


async def payload(store, data):
    return await store.write(
        PAYLOAD_SCOPE, data, content_type="text/plain", serialization_version="1"
    )


async def learned_experience():
    manager, engine, learner = worker()
    verifier = engine.assessment_verifier
    assert verifier is not None
    experiences, assessments = verifier.experiences, verifier.assessments
    payloads = InMemoryPayloadStore()
    event = await submit(manager)
    accepted = await accepted_assessment(event, experiences, assessments)
    await learner.enqueue_assessment(
        event.feedback_id, accepted.assessment_id, assessment_version=accepted.version
    )
    assert await learner.drain() == 1
    (artifact,) = (await engine.repository.query(ArtifactQuery(scope=SCOPE))).artifacts
    current = await experiences.get(PAYLOAD_SCOPE, accepted.experience_id)
    await experiences.update(
        current.model_copy(
            update={
                "version": 2,
                "updated_at": datetime.now(UTC) + timedelta(seconds=1),
                "original_input": await payload(payloads, b"input"),
                "final_output": await payload(payloads, b"output"),
                "feedback_ids": (event.feedback_id,),
            }
        ),
        expected_version=1,
    )
    critique = await payload(payloads, b"critique")
    reviewed = accepted.model_copy(
        update={
            "assessment_id": uuid4(),
            "status": AssessmentStatus.REVIEW_REQUIRED,
            "critique": critique,
        }
    )
    await assessments.create(reviewed)
    await assessments.decide(
        LearningDecision(
            scope=PAYLOAD_SCOPE,
            assessment_id=reviewed.assessment_id,
            assessment_version=1,
            accepted=False,
            reviewer="reviewer",
            reason=await payload(payloads, b"reason"),
        )
    )
    await assessments.create(
        reviewed.model_copy(update={"assessment_id": uuid4(), "critique": None})
    )
    unrelated = await experiences.create(
        ExecutionExperience(
            scope=PAYLOAD_SCOPE,
            execution_id="unrelated",
            original_input=await payload(payloads, b"kept"),
        )
    )
    purger = ExperiencePurger(experiences, payloads, assessments=assessments, engine=engine)
    return purger, engine, accepted, artifact, unrelated


async def test_purge_removes_records_and_invalidates_learning_with_safe_retry():
    purger, engine, accepted, artifact, unrelated = await learned_experience()
    other = PAYLOAD_SCOPE.model_copy(update={"agent_id": "other"})
    isolated = await purger.purge(other, accepted.experience_id)
    assert not isolated.experience_deleted
    assert (isolated.payloads_deleted, isolated.assessments_deleted) == (0, 0)
    assert await purger.experiences.get(PAYLOAD_SCOPE, accepted.experience_id)

    assessments = purger.assessments
    assert assessments is not None
    with (
        patch.object(assessments, "delete_for_experience", side_effect=OSError("interrupted")),
        pytest.raises(OSError, match="interrupted"),
    ):
        await purger.purge(PAYLOAD_SCOPE, accepted.experience_id)
    assert await purger.experiences.get(PAYLOAD_SCOPE, accepted.experience_id)
    current = await engine.repository.get(SCOPE, artifact.artifact_id)
    assert current.evidence_count == 0

    with patch("feedback_manager.storage.purge._PAGE", 1):
        report = await purger.purge(PAYLOAD_SCOPE, accepted.experience_id)
    assert report.experience_deleted
    assert (report.assessments_deleted, report.decisions_deleted) == (3, 2)
    assert report.payloads_deleted == 0
    assert report.feedback_ids == (accepted.feedback_id,)
    assert report.artifact_ids == (artifact.artifact_id,)
    with pytest.raises(ExperienceNotFoundError):
        await purger.experiences.get(PAYLOAD_SCOPE, accepted.experience_id)
    with pytest.raises(AssessmentNotFoundError):
        await assessments.get(PAYLOAD_SCOPE, accepted.assessment_id)
    (kept,) = unrelated.payload_references()
    assert await purger.payloads.read(PAYLOAD_SCOPE, kept.payload_id) == b"kept"
    assert (await engine.repository.get(SCOPE, artifact.artifact_id)).version == current.version

    repeated = await purger.purge(PAYLOAD_SCOPE, accepted.experience_id)
    assert not repeated.experience_deleted
    assert repeated.feedback_ids == repeated.artifact_ids == ()


async def test_purge_deletes_every_referenced_payload():
    purger, _, accepted, _, _ = await learned_experience()
    experience = await purger.experiences.get(PAYLOAD_SCOPE, accepted.experience_id)
    assessments = purger.assessments
    assert assessments is not None
    references = list(experience.payload_references())
    for item in (
        await assessments.for_experience(PAYLOAD_SCOPE, accepted.experience_id)
    ).assessments:
        references.extend(item.payload_references())
        decision = await assessments.decision(
            PAYLOAD_SCOPE, item.assessment_id, assessment_version=1
        )
        if decision is not None and decision.reason is not None:
            references.append(decision.reason)
    assert len(references) == 4
    report = await purger.purge(PAYLOAD_SCOPE, accepted.experience_id)
    assert report.payloads_deleted == 4
    for reference in references:
        with pytest.raises(PayloadNotFoundError):
            await purger.payloads.read(PAYLOAD_SCOPE, reference.payload_id)


async def test_purge_without_optional_components_and_input_validation():
    purger, _, accepted, _, _ = await learned_experience()
    minimal = ExperiencePurger(purger.experiences, purger.payloads)
    report = await minimal.purge(PAYLOAD_SCOPE, accepted.experience_id)
    assert report.experience_deleted
    assert report.payloads_deleted == 2
    assert report.artifact_ids == ()
    assert report.assessments_deleted == 0
    assert purger.assessments is not None
    assert await purger.assessments.get(PAYLOAD_SCOPE, accepted.assessment_id)
    with pytest.raises(TypeError, match="experience_id"):
        await minimal.purge(PAYLOAD_SCOPE, "id")  # type: ignore[arg-type]
    for arguments, message in (
        (("experiences", purger.payloads), "experiences"),
        ((purger.experiences, "payloads"), "payloads"),
    ):
        with pytest.raises(TypeError, match=message):
            ExperiencePurger(*arguments)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="assessments"):
        ExperiencePurger(purger.experiences, purger.payloads, assessments="x")  # type: ignore[arg-type]
