"""Synthetic, explicitly reviewed assessment setup for runnable examples."""

from uuid import NAMESPACE_URL, uuid5

from feedback_manager.assessment import (
    AssessmentEvidence,
    AssessmentStatus,
    ConservativeFeedbackCritic,
    LearningDecision,
)
from feedback_manager.core.events import FeedbackEvent
from feedback_manager.experience import ExecutionExperience
from feedback_manager.learning.assessment_verification import AssessmentVerifier
from feedback_manager.learning.models import ArtifactScope
from feedback_manager.storage.assessment import AssessmentNotFoundError
from feedback_manager.storage.experience import ExperienceNotFoundError
from feedback_manager.storage.payload import PayloadScope


async def assess_demo_feedback(
    events: list[FeedbackEvent], scope: ArtifactScope, verifier: AssessmentVerifier
):
    """Simulate human review of two distinct synthetic execution episodes."""
    if len(events) < 2:
        raise ValueError("the demo needs at least two independent episodes")
    payload_scope = PayloadScope.model_validate(scope.model_dump())
    experiences = []
    for event in events:
        if event.execution_context is None or event.execution_context.run_id is None:
            raise ValueError("demo feedback needs an execution ID")
        identifier = uuid5(NAMESPACE_URL, f"feedback-manager:demo-experience:{event.feedback_id}")
        try:
            experience = await verifier.experiences.get(payload_scope, identifier)
        except ExperienceNotFoundError:
            experience = await verifier.experiences.create(
                ExecutionExperience(
                    experience_id=identifier,
                    scope=payload_scope,
                    execution_id=event.execution_context.run_id,
                )
            )
        experiences.append(experience)
    assessments = []
    for index, event in enumerate(events):
        experience = experiences[index]
        supporting = experiences[(index + 1) % len(experiences)]
        identifier = uuid5(NAMESPACE_URL, f"feedback-manager:demo-assessment:{event.feedback_id}")
        try:
            assessment = await verifier.assessments.get(payload_scope, identifier)
        except AssessmentNotFoundError:
            evidence = AssessmentEvidence(
                evidence_id=str(supporting.experience_id),
                scope=payload_scope,
                experience_id=supporting.experience_id,
                experience_version=supporting.version,
            )
            finding = await ConservativeFeedbackCritic().assess(
                event, experience, supporting=(evidence,)
            )
            assessment = await verifier.assessments.create(
                finding.model_copy(
                    update={
                        "assessment_id": identifier,
                        "status": AssessmentStatus.SUPPORTED,
                        "reviewer": "synthetic-human-reviewer",
                    }
                )
            )
        decision = await verifier.assessments.decision(
            payload_scope, identifier, assessment_version=assessment.version
        )
        if decision is None:
            await verifier.assessments.decide(
                LearningDecision(
                    scope=payload_scope,
                    assessment_id=identifier,
                    assessment_version=assessment.version,
                    accepted=True,
                    reviewer="synthetic-human-reviewer",
                )
            )
        assessments.append(assessment)
    return assessments
