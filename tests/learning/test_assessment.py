from uuid import uuid4

import pytest
from pydantic import ValidationError

from feedback_manager import ExecutionContext, FeedbackEvent, FeedbackStatus, FeedbackTarget
from feedback_manager.assessment import (
    AssessmentEvidence,
    AssessmentStatus,
    ConservativeFeedbackCritic,
    FeedbackAssessment,
    FeedbackCritic,
    LearningDecision,
)
from feedback_manager.experience import ExecutionExperience
from feedback_manager.learning.errors import LearningScopeError, LearningValidationError
from feedback_manager.storage.payload import InMemoryPayloadStore, PayloadScope

SCOPE = PayloadScope(tenant_id="tenant", namespace="app", agent_id="agent")


def sources() -> tuple[FeedbackEvent, ExecutionExperience]:
    feedback = FeedbackEvent(
        source="human",
        category="correction",
        target=FeedbackTarget(type="run", id="run"),
        status=FeedbackStatus.RECEIVED,
        payload={"corrected_output": {"rows": [1, 2]}},
        execution_context=ExecutionContext(tenant_id="tenant", agent_id="agent", run_id="run"),
    )
    return feedback, ExecutionExperience(scope=SCOPE, execution_id="run")


async def test_default_critic_preserves_evidence_but_does_not_infer_truth() -> None:
    feedback, experience = sources()
    supporting = AssessmentEvidence(
        evidence_id="support",
        scope=SCOPE,
        experience_id=uuid4(),
        experience_version=1,
    )
    contradictory = supporting.model_copy(update={"evidence_id": "contradiction"})
    critic: FeedbackCritic = ConservativeFeedbackCritic()
    result = await critic.assess(
        feedback, experience, supporting=(supporting,), contradictory=(contradictory,)
    )
    assert result.status is AssessmentStatus.REVIEW_REQUIRED
    assert result.supporting_evidence == (supporting,)
    assert result.contradictory_evidence == (contradictory,)
    assert "reported_contradictory_evidence" in result.reasons
    assert result.confidence is None
    assert result.uncertainty is None
    assert FeedbackAssessment.model_validate_json(result.model_dump_json()) == result


async def test_missing_correction_and_snapshot_changes_are_visible() -> None:
    feedback, experience = sources()
    critic = ConservativeFeedbackCritic()
    original = await critic.assess(feedback, experience)
    missing = await critic.assess(feedback.model_copy(update={"payload": {}}), experience)
    assert missing.status is AssessmentStatus.INCOMPLETE
    assert "missing_correction" in missing.reasons
    assert original.feedback_digest != missing.feedback_digest
    changed = await critic.assess(feedback, experience.model_copy(update={"status": "completed"}))
    assert original.experience_digest != changed.experience_digest
    empty = await critic.assess(
        feedback.model_copy(update={"payload": {"correction": ""}}), experience
    )
    assert empty.status is AssessmentStatus.REVIEW_REQUIRED
    no_context = await critic.assess(
        feedback.model_copy(update={"execution_context": None}), experience
    )
    assert no_context.status is AssessmentStatus.REVIEW_REQUIRED


async def test_critic_rejects_mismatched_sources_and_invalid_feedback() -> None:
    feedback, experience = sources()
    critic = ConservativeFeedbackCritic()
    with pytest.raises(LearningValidationError, match="received"):
        await critic.assess(
            feedback.model_copy(update={"status": FeedbackStatus.CREATED}), experience
        )
    with pytest.raises(LearningScopeError, match="target"):
        await critic.assess(
            feedback.model_copy(update={"target": FeedbackTarget(type="run", id="other")}),
            experience,
        )
    for update in (
        {"tenant_id": "other"},
        {"agent_id": "other"},
        {"run_id": "other"},
    ):
        assert feedback.execution_context is not None
        foreign = feedback.model_copy(
            update={"execution_context": feedback.execution_context.model_copy(update=update)}
        )
        with pytest.raises(LearningScopeError):
            await critic.assess(foreign, experience)
    invalid = feedback.model_copy(deep=True)
    invalid.payload["bad"] = {1, 2}  # type: ignore[assignment]
    with pytest.raises(LearningValidationError, match="snapshot is invalid"):
        await critic.assess(invalid, experience)


async def test_assessment_rejects_foreign_or_duplicate_evidence() -> None:
    feedback, experience = sources()
    foreign = PayloadScope(tenant_id="other", namespace="app", agent_id="agent")
    evidence = AssessmentEvidence(
        evidence_id="evidence",
        scope=foreign,
        experience_id=uuid4(),
        experience_version=1,
    )
    critic = ConservativeFeedbackCritic()
    with pytest.raises(ValidationError, match="evidence scope"):
        await critic.assess(feedback, experience, supporting=(evidence,))
    evidence = evidence.model_copy(update={"scope": SCOPE})
    with pytest.raises(ValidationError, match="unique"):
        await critic.assess(feedback, experience, supporting=(evidence,), contradictory=(evidence,))
    with pytest.raises(LearningValidationError, match="bound"):
        await critic.assess(feedback, experience, supporting=(evidence,) * 101)

    payloads = InMemoryPayloadStore()
    reference = await payloads.write(
        foreign, b"private", content_type="text/plain", serialization_version="1"
    )
    with pytest.raises(ValidationError, match="provenance scope"):
        AssessmentEvidence(
            evidence_id="evidence",
            scope=SCOPE,
            experience_id=uuid4(),
            experience_version=1,
            provenance=reference,
        )
    assessment = await critic.assess(feedback, experience)
    with pytest.raises(ValidationError, match="payload scope"):
        FeedbackAssessment.model_validate({**assessment.model_dump(), "critique": reference})
    with pytest.raises(ValidationError, match="reason scope"):
        LearningDecision(
            scope=SCOPE,
            assessment_id=assessment.assessment_id,
            assessment_version=1,
            accepted=False,
            reviewer="reviewer",
            reason=reference,
        )
