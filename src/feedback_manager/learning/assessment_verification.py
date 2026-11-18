"""Verify current, explicitly accepted assessment and source snapshots."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from feedback_manager.assessment import (
    AssessmentStatus,
    _digest,
    _snapshot_feedback,
    _validate_sources,
)
from feedback_manager.core.status import FeedbackStatus
from feedback_manager.core.targets import FeedbackTargetType
from feedback_manager.learning.errors import LearningScopeError, LearningValidationError
from feedback_manager.learning.signals import ExtractionDisposition, LearningSignalExtractor
from feedback_manager.storage.assessment import AssessmentNotFoundError, _validate_decision
from feedback_manager.storage.experience import ExperienceNotFoundError
from feedback_manager.storage.payload import PayloadScope

if TYPE_CHECKING:
    from uuid import UUID

    from feedback_manager.contracts.store import FeedbackStore
    from feedback_manager.core.events import FeedbackEvent
    from feedback_manager.experience import ExecutionExperience
    from feedback_manager.learning.models import LearnedArtifact, LearningSignal
    from feedback_manager.learning.signals import SignalExtractor
    from feedback_manager.storage.assessment import AssessmentRepositoryABC
    from feedback_manager.storage.experience import ExperienceRepositoryABC


class AssessmentVerification(BaseModel):
    """Whether a current assessment is accepted, with a non-sensitive reason."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    eligible: bool
    reason: str


class AssessmentVerifier:
    """Check current authoritative snapshots and a separate learning decision.

    The host authenticates the scope and owns all stores. Reads across stores
    are not one transaction; a host must propagate later source changes and
    recheck this result before applying or exposing derived artifacts.
    """

    def __init__(
        self,
        feedback: FeedbackStore,
        experiences: ExperienceRepositoryABC,
        assessments: AssessmentRepositoryABC,
        *,
        extractor: SignalExtractor | None = None,
    ) -> None:
        self.feedback = feedback
        self.experiences = experiences
        self.assessments = assessments
        self.extractor = extractor if extractor is not None else LearningSignalExtractor()

    async def verify(
        self,
        scope: PayloadScope,
        assessment_id: UUID,
        *,
        feedback_id: UUID,
        assessment_version: int | None = None,
    ) -> AssessmentVerification:
        """Verify the current decision and exact feedback and experience versions."""
        try:
            assessment = await self.assessments.get(scope, assessment_id)
        except AssessmentNotFoundError:
            return _denied("assessment_not_found")
        if assessment.scope != scope or assessment.assessment_id != assessment_id:
            raise LearningScopeError("assessment repository returned an out-of-scope assessment")
        if assessment.feedback_id != feedback_id:
            return _denied("feedback_mismatch")
        if assessment_version is not None and assessment.version != assessment_version:
            return _denied("assessment_version_changed")
        if assessment.status is not AssessmentStatus.SUPPORTED:
            return _denied("assessment_not_supported")
        if assessment.assessed_at > datetime.now(UTC):
            return _denied("assessment_in_future")
        decision = await self.assessments.decision(
            scope, assessment_id, assessment_version=assessment.version
        )
        if decision is None:
            return _denied("decision_missing")
        if decision.scope != scope or decision.assessment_id != assessment_id:
            raise LearningScopeError("assessment repository returned an out-of-scope decision")
        _validate_decision(assessment, decision)
        if not decision.accepted:
            return _denied("decision_rejected")
        if decision.decided_at > datetime.now(UTC):
            return _denied("decision_in_future")
        feedback = await self.feedback.get(feedback_id)
        if feedback is None:
            return _denied("feedback_not_found")
        if feedback.feedback_id != feedback_id:
            raise LearningValidationError("feedback store returned an unexpected feedback ID")
        try:
            experience = await self.experiences.get(scope, assessment.experience_id)
        except ExperienceNotFoundError:
            return _denied("experience_not_found")
        if experience.scope != scope or experience.experience_id != assessment.experience_id:
            raise LearningScopeError("experience repository returned an out-of-scope experience")
        if experience.version != assessment.experience_version:
            return _denied("experience_changed")
        independent = False
        for item in assessment.supporting_evidence:
            if item.experience_id == assessment.experience_id or item.feedback_id == feedback_id:
                continue
            try:
                supporting = await self.experiences.get(scope, item.experience_id)
            except ExperienceNotFoundError:
                return _denied("supporting_experience_not_found")
            if supporting.scope != scope or supporting.experience_id != item.experience_id:
                raise LearningScopeError("experience repository returned out-of-scope evidence")
            if supporting.version != item.experience_version:
                return _denied("supporting_experience_changed")
            if (
                supporting.execution_id != experience.execution_id
                and feedback_id not in supporting.feedback_ids
            ):
                independent = True
        if not independent:
            return _denied("insufficient_independent_support")
        reason = await asyncio.to_thread(
            _check_sources,
            feedback,
            experience,
            assessment.feedback_digest,
            assessment.experience_digest,
        )
        if reason is not None:
            return _denied(reason)
        current = await self.assessments.get(scope, assessment_id)
        if current != assessment:
            return _denied("assessment_changed")
        return AssessmentVerification(eligible=True, reason="accepted")

    async def verify_signal(self, signal: LearningSignal) -> AssessmentVerification:
        """Verify an accepted assessment and the exact signal extracted from its feedback."""
        evidence = signal.evidence
        if (
            evidence.feedback_id is None
            or evidence.assessment_id is None
            or evidence.assessment_version is None
        ):
            return _denied("assessment_reference_missing")
        scope = PayloadScope.model_validate(signal.scope.model_dump())
        result = await self.verify(
            scope,
            evidence.assessment_id,
            feedback_id=evidence.feedback_id,
            assessment_version=evidence.assessment_version,
        )
        if not result.eligible:
            return result
        feedback = await self.feedback.get(evidence.feedback_id)
        if feedback is None:
            return _denied("feedback_not_found")
        extraction = self.extractor.extract(feedback, scope=signal.scope)
        if (
            extraction.disposition is not ExtractionDisposition.EXTRACTED
            or extraction.signal is None
        ):
            return _denied("signal_mismatch")
        expected = extraction.signal
        expected_evidence = expected.evidence.model_copy(
            update={
                "assessment_id": evidence.assessment_id,
                "assessment_version": evidence.assessment_version,
            }
        )
        if signal != expected.model_copy(update={"evidence": expected_evidence}):
            return _denied("signal_mismatch")
        return await self.verify(
            scope,
            evidence.assessment_id,
            feedback_id=evidence.feedback_id,
            assessment_version=evidence.assessment_version,
        )

    async def verify_artifact(self, artifact: LearnedArtifact) -> AssessmentVerification:
        """Recheck every feedback assessment referenced by an artifact."""
        scope = PayloadScope.model_validate(artifact.scope.model_dump())
        for evidence in artifact.evidence:
            if not evidence.valid or evidence.feedback_id is None:
                continue
            if evidence.assessment_id is None or evidence.assessment_version is None:
                return _denied("assessment_reference_missing")
            result = await self.verify(
                scope,
                evidence.assessment_id,
                feedback_id=evidence.feedback_id,
                assessment_version=evidence.assessment_version,
            )
            if not result.eligible:
                return result
            feedback = await self.feedback.get(evidence.feedback_id)
            if feedback is None:
                return _denied("feedback_not_found")
            extraction = self.extractor.extract(feedback, scope=artifact.scope)
            if (
                extraction.disposition is not ExtractionDisposition.EXTRACTED
                or extraction.signal is None
            ):
                return _denied("evidence_mismatch")
            expected = extraction.signal.evidence.model_copy(
                update={
                    "assessment_id": evidence.assessment_id,
                    "assessment_version": evidence.assessment_version,
                    "valid": evidence.valid,
                }
            )
            if evidence != expected:
                return _denied("evidence_mismatch")
            result = await self.verify(
                scope,
                evidence.assessment_id,
                feedback_id=evidence.feedback_id,
                assessment_version=evidence.assessment_version,
            )
            if not result.eligible:
                return result
        return AssessmentVerification(eligible=True, reason="accepted")


def _check_sources(
    feedback: FeedbackEvent,
    experience: ExecutionExperience,
    feedback_digest: str,
    experience_digest: str,
) -> str | None:
    feedback = _snapshot_feedback(feedback)
    if feedback.status is FeedbackStatus.CREATED:
        return "feedback_not_received"
    _validate_sources(feedback, experience)
    context = feedback.execution_context
    if (
        (context is None or context.run_id is None)
        and feedback.target.type != FeedbackTargetType.RUN
        and feedback.feedback_id not in experience.feedback_ids
    ):
        return "feedback_experience_link_unverified"
    if _digest(feedback) != feedback_digest:
        return "feedback_changed"
    if _digest(experience) != experience_digest:
        return "experience_changed"
    return None


def _denied(reason: str) -> AssessmentVerification:
    return AssessmentVerification(eligible=False, reason=reason)


__all__ = ["AssessmentVerification", "AssessmentVerifier"]
