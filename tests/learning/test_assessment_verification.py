from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest

from feedback_manager import FeedbackStatus, FeedbackTarget
from feedback_manager.assessment import (
    AssessmentEvidence,
    AssessmentStatus,
    ConservativeFeedbackCritic,
    LearningDecision,
)
from feedback_manager.learning.assessment_verification import AssessmentVerifier
from feedback_manager.learning.errors import LearningScopeError, LearningValidationError
from feedback_manager.storage import InMemoryFeedbackStore
from feedback_manager.storage.assessment import InMemoryAssessmentRepository
from feedback_manager.storage.experience import InMemoryExperienceRepository
from tests.learning.test_assessment import SCOPE, sources


async def verified_setup(*, text_correction: bool = False):
    event, experience = sources()
    if text_correction:
        event = event.model_copy(update={"payload": {"correction": "corrected result"}})
    supporting = experience.model_copy(
        update={"experience_id": uuid4(), "execution_id": "independent"}
    )
    evidence = AssessmentEvidence(
        evidence_id="independent",
        scope=SCOPE,
        experience_id=supporting.experience_id,
        experience_version=1,
    )
    assessment = (
        await ConservativeFeedbackCritic().assess(event, experience, supporting=(evidence,))
    ).model_copy(update={"status": AssessmentStatus.SUPPORTED})
    feedback, experiences, assessments = (
        InMemoryFeedbackStore(),
        InMemoryExperienceRepository(),
        InMemoryAssessmentRepository(),
    )
    await feedback.create(event)
    await experiences.create(experience)
    await experiences.create(supporting)
    await assessments.create(assessment)
    decision = LearningDecision(
        scope=SCOPE,
        assessment_id=assessment.assessment_id,
        assessment_version=1,
        accepted=True,
        reviewer="reviewer",
    )
    await assessments.decide(decision)
    return (
        AssessmentVerifier(feedback, experiences, assessments),
        event,
        experience,
        supporting,
        assessment,
        decision,
    )


async def test_verifier_requires_support_separate_decision_and_current_sources() -> None:
    event, experience = sources()
    feedback = InMemoryFeedbackStore()
    experiences = InMemoryExperienceRepository()
    assessments = InMemoryAssessmentRepository()
    verifier = AssessmentVerifier(feedback, experiences, assessments)
    critic = ConservativeFeedbackCritic()
    supporting = experience.model_copy(
        update={"experience_id": uuid4(), "execution_id": "independent"}
    )
    evidence = AssessmentEvidence(
        evidence_id="independent",
        scope=SCOPE,
        experience_id=supporting.experience_id,
        experience_version=1,
    )
    assessment = await critic.assess(event, experience, supporting=(evidence,))
    assessment = assessment.model_copy(
        update={"assessed_at": assessment.assessed_at - timedelta(seconds=1)}
    )
    await feedback.create(event)
    await experiences.create(experience)
    await experiences.create(supporting)
    assert (await verifier.verify(SCOPE, uuid4(), feedback_id=event.feedback_id)).reason == (
        "assessment_not_found"
    )
    await assessments.create(assessment)
    assert (await verifier.verify(SCOPE, assessment.assessment_id, feedback_id=uuid4())).reason == (
        "feedback_mismatch"
    )
    assert (
        await verifier.verify(SCOPE, assessment.assessment_id, feedback_id=event.feedback_id)
    ).reason == "assessment_not_supported"
    supported = assessment.model_copy(
        update={
            "status": AssessmentStatus.SUPPORTED,
            "version": 2,
            "assessed_at": assessment.assessed_at + timedelta(microseconds=1),
        }
    )
    await assessments.update(supported, expected_version=1)
    assert (
        await verifier.verify(SCOPE, supported.assessment_id, feedback_id=event.feedback_id)
    ).reason == "decision_missing"
    decision = LearningDecision(
        scope=SCOPE,
        assessment_id=supported.assessment_id,
        assessment_version=2,
        accepted=True,
        reviewer="independent-reviewer",
        decided_at=supported.assessed_at,
    )
    await assessments.decide(decision)
    result = await verifier.verify(SCOPE, supported.assessment_id, feedback_id=event.feedback_id)
    assert result.eligible
    await feedback.transition(
        event.feedback_id, FeedbackStatus.ACKNOWLEDGED, expected=FeedbackStatus.RECEIVED
    )
    assert (
        await verifier.verify(SCOPE, supported.assessment_id, feedback_id=event.feedback_id)
    ).reason == "feedback_changed"
    updated = experience.model_copy(
        update={"version": 2, "updated_at": experience.updated_at + timedelta(microseconds=1)}
    )
    await experiences.update(updated, expected_version=1)
    assert (
        await verifier.verify(SCOPE, supported.assessment_id, feedback_id=event.feedback_id)
    ).reason == "experience_changed"


async def test_verifier_requires_explicit_feedback_experience_link() -> None:
    event, experience = sources()
    event = event.model_copy(
        update={"target": FeedbackTarget(type="message", id="message"), "execution_context": None}
    )
    feedback = InMemoryFeedbackStore()
    experiences = InMemoryExperienceRepository()
    assessments = InMemoryAssessmentRepository()
    verifier = AssessmentVerifier(feedback, experiences, assessments)
    supporting = experience.model_copy(
        update={"experience_id": uuid4(), "execution_id": "independent"}
    )
    evidence = AssessmentEvidence(
        evidence_id="independent",
        scope=SCOPE,
        experience_id=supporting.experience_id,
        experience_version=1,
    )
    assessment = (
        await ConservativeFeedbackCritic().assess(event, experience, supporting=(evidence,))
    ).model_copy(update={"status": AssessmentStatus.SUPPORTED})
    await feedback.create(event)
    await experiences.create(experience)
    await experiences.create(supporting)
    await assessments.create(assessment)
    await assessments.decide(
        LearningDecision(
            scope=SCOPE,
            assessment_id=assessment.assessment_id,
            assessment_version=1,
            accepted=True,
            reviewer="reviewer",
        )
    )
    result = await verifier.verify(SCOPE, assessment.assessment_id, feedback_id=event.feedback_id)
    assert not result.eligible
    assert result.reason == "feedback_experience_link_unverified"


@pytest.mark.parametrize(
    ("accepted", "reason"), [(False, "decision_rejected"), (True, "feedback_not_found")]
)
async def test_verifier_reports_missing_or_rejected_inputs(accepted: bool, reason: str) -> None:
    event, experience = sources()
    feedback = InMemoryFeedbackStore()
    experiences = InMemoryExperienceRepository()
    assessments = InMemoryAssessmentRepository()
    evidence = AssessmentEvidence(
        evidence_id="independent", scope=SCOPE, experience_id=uuid4(), experience_version=1
    )
    assessment = (
        await ConservativeFeedbackCritic().assess(event, experience, supporting=(evidence,))
    ).model_copy(update={"status": AssessmentStatus.SUPPORTED})
    await assessments.create(assessment)
    await assessments.decide(
        LearningDecision(
            scope=SCOPE,
            assessment_id=assessment.assessment_id,
            assessment_version=1,
            accepted=accepted,
            reviewer="reviewer",
        )
    )
    result = await AssessmentVerifier(feedback, experiences, assessments).verify(
        SCOPE, assessment.assessment_id, feedback_id=event.feedback_id
    )
    assert not result.eligible
    assert result.reason == reason


async def test_verifier_rejects_missing_stale_and_nonindependent_support() -> None:
    verifier, event, experience, supporting, assessment, _ = await verified_setup()
    self_evidence = AssessmentEvidence(
        evidence_id="same-source",
        scope=SCOPE,
        experience_id=experience.experience_id,
        experience_version=experience.version,
        feedback_id=event.feedback_id,
    )
    with patch.object(
        verifier.assessments,
        "get",
        return_value=assessment.model_copy(
            update={"supporting_evidence": (self_evidence, *assessment.supporting_evidence)}
        ),
    ):
        assert (
            await verifier.verify(SCOPE, assessment.assessment_id, feedback_id=event.feedback_id)
        ).eligible
    empty = InMemoryExperienceRepository()
    await empty.create(experience)
    verifier.experiences = empty
    result = await verifier.verify(SCOPE, assessment.assessment_id, feedback_id=event.feedback_id)
    assert result.reason == "supporting_experience_not_found"
    await empty.create(supporting)
    revised = supporting.model_copy(
        update={"version": 2, "updated_at": supporting.updated_at + timedelta(microseconds=1)}
    )
    await empty.update(revised, expected_version=1)
    result = await verifier.verify(SCOPE, assessment.assessment_id, feedback_id=event.feedback_id)
    assert result.reason == "supporting_experience_changed"

    identical_run = supporting.model_copy(update={"execution_id": experience.execution_id})
    with patch.object(verifier.experiences, "get", side_effect=[experience, identical_run]):
        result = await verifier.verify(
            SCOPE, assessment.assessment_id, feedback_id=event.feedback_id
        )
    assert result.reason == "insufficient_independent_support"
    verifier.experiences = InMemoryExperienceRepository()
    result = await verifier.verify(SCOPE, assessment.assessment_id, feedback_id=event.feedback_id)
    assert result.reason == "experience_not_found"


async def test_verifier_rejects_future_and_changed_records() -> None:
    verifier, event, experience, supporting, assessment, decision = await verified_setup()
    future = datetime.now(UTC) + timedelta(days=1)
    with patch.object(
        verifier.assessments,
        "get",
        return_value=assessment.model_copy(update={"assessed_at": future}),
    ):
        result = await verifier.verify(
            SCOPE, assessment.assessment_id, feedback_id=event.feedback_id
        )
    assert result.reason == "assessment_in_future"
    with patch.object(
        verifier.assessments,
        "decision",
        return_value=decision.model_copy(update={"decided_at": future}),
    ):
        result = await verifier.verify(
            SCOPE, assessment.assessment_id, feedback_id=event.feedback_id
        )
    assert result.reason == "decision_in_future"
    changed = experience.model_copy(update={"status": "changed"})
    with patch.object(verifier.experiences, "get", side_effect=[changed, supporting]):
        result = await verifier.verify(
            SCOPE, assessment.assessment_id, feedback_id=event.feedback_id
        )
    assert result.reason == "experience_changed"
    with patch.object(
        verifier.feedback,
        "get",
        return_value=event.model_copy(update={"status": FeedbackStatus.CREATED}),
    ):
        result = await verifier.verify(
            SCOPE, assessment.assessment_id, feedback_id=event.feedback_id
        )
    assert result.reason == "feedback_not_received"
    with patch.object(
        verifier.assessments,
        "get",
        side_effect=[assessment, assessment.model_copy(update={"version": 2})],
    ):
        result = await verifier.verify(
            SCOPE, assessment.assessment_id, feedback_id=event.feedback_id
        )
    assert result.reason == "assessment_changed"


async def test_verifier_rejects_wrong_scope_and_provider_identity() -> None:
    verifier, event, experience, supporting, assessment, decision = await verified_setup()
    with (
        patch.object(
            verifier.assessments,
            "get",
            return_value=assessment.model_copy(update={"assessment_id": uuid4()}),
        ),
        pytest.raises(LearningScopeError),
    ):
        await verifier.verify(SCOPE, assessment.assessment_id, feedback_id=event.feedback_id)
    with (
        patch.object(
            verifier.assessments,
            "decision",
            return_value=decision.model_copy(update={"assessment_id": uuid4()}),
        ),
        pytest.raises(LearningScopeError),
    ):
        await verifier.verify(SCOPE, assessment.assessment_id, feedback_id=event.feedback_id)
    with (
        patch.object(
            verifier.feedback, "get", return_value=event.model_copy(update={"feedback_id": uuid4()})
        ),
        pytest.raises(LearningValidationError),
    ):
        await verifier.verify(SCOPE, assessment.assessment_id, feedback_id=event.feedback_id)
    with (
        patch.object(
            verifier.experiences,
            "get",
            return_value=experience.model_copy(update={"experience_id": uuid4()}),
        ),
        pytest.raises(LearningScopeError),
    ):
        await verifier.verify(SCOPE, assessment.assessment_id, feedback_id=event.feedback_id)
    with (
        patch.object(
            verifier.experiences,
            "get",
            side_effect=[experience, supporting.model_copy(update={"experience_id": uuid4()})],
        ),
        pytest.raises(LearningScopeError),
    ):
        await verifier.verify(SCOPE, assessment.assessment_id, feedback_id=event.feedback_id)
