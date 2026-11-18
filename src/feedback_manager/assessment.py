"""Independent, scoped feedback assessments and conservative critic contracts."""

from __future__ import annotations

import asyncio
import hashlib
import json
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import PydanticSerializationError

from feedback_manager.core._types import Identifier
from feedback_manager.core.categories import FeedbackCategory
from feedback_manager.core.events import FeedbackEvent
from feedback_manager.core.status import FeedbackStatus
from feedback_manager.core.targets import FeedbackTargetType
from feedback_manager.experience import ExecutionExperience
from feedback_manager.learning.errors import LearningScopeError, LearningValidationError
from feedback_manager.learning.models import ScopeVisibility
from feedback_manager.storage.payload import PayloadMetadata, PayloadScope

if TYPE_CHECKING:
    from collections.abc import Sequence


class AssessmentStatus(StrEnum):
    """A critic's finding, independent of acceptance or artifact approval."""

    SUPPORTED = "supported"
    CHALLENGED = "challenged"
    INCOMPLETE = "incomplete"
    REVIEW_REQUIRED = "review_required"


class AssessmentEvidence(BaseModel):
    """An attributed experience reference; its asserted relation is not proof."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    evidence_id: Identifier
    scope: PayloadScope
    experience_id: UUID
    experience_version: int = Field(ge=1, strict=True)
    feedback_id: UUID | None = None
    provenance: PayloadMetadata | None = None

    @model_validator(mode="after")
    def _validate_scope(self) -> AssessmentEvidence:
        if self.provenance is not None and self.provenance.scope != self.scope:
            raise ValueError("evidence provenance scope must match")
        return self


class FeedbackAssessment(BaseModel):
    """An immutable critic finding tied to exact source snapshots.

    Evidence references and critique payloads remain scoped. An assessment
    alone does not accept feedback for learning or approve an artifact.
    """

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always", allow_inf_nan=False
    )

    assessment_id: UUID = Field(default_factory=uuid4)
    feedback_id: UUID
    feedback_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    experience_id: UUID
    experience_version: int = Field(ge=1, strict=True)
    experience_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: PayloadScope
    status: AssessmentStatus
    method: Identifier
    reviewer: Identifier
    critique: PayloadMetadata | None = None
    supporting_evidence: tuple[AssessmentEvidence, ...] = Field(default=(), max_length=100)
    contradictory_evidence: tuple[AssessmentEvidence, ...] = Field(default=(), max_length=100)
    confidence: float | None = Field(default=None, ge=0, le=1)
    uncertainty: float | None = Field(default=None, ge=0, le=1)
    reasons: tuple[Identifier, ...] = ()
    provenance: PayloadMetadata | None = None
    assessed_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    version: int = Field(default=1, ge=1, strict=True)

    @model_validator(mode="after")
    def _validate_references(self) -> FeedbackAssessment:
        evidence = (*self.supporting_evidence, *self.contradictory_evidence)
        if any(item.scope != self.scope for item in evidence):
            raise ValueError("assessment evidence scope must match")
        if len({item.evidence_id for item in evidence}) != len(evidence):
            raise ValueError("assessment evidence IDs must be unique")
        if any(
            item is not None and item.scope != self.scope
            for item in (self.critique, self.provenance)
        ):
            raise ValueError("assessment payload scope must match")
        return self

    def payload_references(self) -> tuple[PayloadMetadata, ...]:
        """Return the critique, provenance and evidence provenance payloads."""
        references = (
            self.critique,
            self.provenance,
            *(item.provenance for item in self.supporting_evidence),
            *(item.provenance for item in self.contradictory_evidence),
        )
        return tuple(reference for reference in references if reference is not None)


class LearningDecision(BaseModel):
    """A separate host-authorized decision on one assessment version."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    decision_id: UUID = Field(default_factory=uuid4)
    scope: PayloadScope
    assessment_id: UUID
    assessment_version: int = Field(ge=1, strict=True)
    accepted: bool = Field(strict=True)
    reviewer: Identifier
    reason: PayloadMetadata | None = None
    decided_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _validate_reason_scope(self) -> LearningDecision:
        if self.reason is not None and self.reason.scope != self.scope:
            raise ValueError("decision reason scope must match")
        return self


class FeedbackCritic(ABC):
    """Application-replaceable critic; hosts authorize all supplied scope."""

    @abstractmethod
    async def assess(
        self,
        feedback: FeedbackEvent,
        experience: ExecutionExperience,
        *,
        supporting: Sequence[AssessmentEvidence] = (),
        contradictory: Sequence[AssessmentEvidence] = (),
    ) -> FeedbackAssessment:
        """Return a finding without modifying feedback or accepting it for learning."""


class ConservativeFeedbackCritic(FeedbackCritic):
    """Detect missing corrections and otherwise require independent human review.

    Evidence labels and retrieval scores are not treated as verified claims.
    Applications can inject a critic that performs domain-specific assessment.
    """

    async def assess(
        self,
        feedback: FeedbackEvent,
        experience: ExecutionExperience,
        *,
        supporting: Sequence[AssessmentEvidence] = (),
        contradictory: Sequence[AssessmentEvidence] = (),
    ) -> FeedbackAssessment:
        """Produce a conservative, snapshot-bound finding."""
        return await asyncio.to_thread(
            self._assess, feedback, experience, supporting, contradictory
        )

    def _assess(
        self,
        feedback: FeedbackEvent,
        experience: ExecutionExperience,
        supporting: Sequence[AssessmentEvidence],
        contradictory: Sequence[AssessmentEvidence],
    ) -> FeedbackAssessment:
        feedback = _snapshot_feedback(feedback)
        experience = ExecutionExperience.model_validate(experience.model_dump())
        if len(supporting) > 100 or len(contradictory) > 100:
            raise LearningValidationError("assessment evidence exceeds the configured bound")
        if feedback.status is FeedbackStatus.CREATED:
            raise LearningValidationError("feedback must be received before assessment")
        _validate_sources(feedback, experience)
        correction_missing = feedback.category == FeedbackCategory.CORRECTION and not any(
            key in feedback.payload and feedback.payload[key] is not None
            for key in ("corrected_output", "corrected_text", "correction")
        )
        reasons = []
        if correction_missing:
            reasons.append("missing_correction")
        if contradictory:
            reasons.append("reported_contradictory_evidence")
        reasons.append("independent_content_review_required")
        return FeedbackAssessment(
            feedback_id=feedback.feedback_id,
            feedback_digest=_digest(feedback),
            experience_id=experience.experience_id,
            experience_version=experience.version,
            experience_digest=_digest(experience),
            scope=experience.scope,
            status=AssessmentStatus.INCOMPLETE
            if correction_missing
            else AssessmentStatus.REVIEW_REQUIRED,
            method="deterministic-conservative-v1",
            reviewer="rule-based",
            supporting_evidence=tuple(supporting),
            contradictory_evidence=tuple(contradictory),
            reasons=tuple(reasons),
        )


def _digest(model: BaseModel) -> str:
    serialized = json.dumps(
        model.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _snapshot_feedback(feedback: FeedbackEvent) -> FeedbackEvent:
    try:
        return FeedbackEvent.model_validate(feedback.model_dump(warnings="error"))
    except (ValidationError, PydanticSerializationError):
        raise LearningValidationError("feedback snapshot is invalid") from None


def _validate_sources(feedback: FeedbackEvent, experience: ExecutionExperience) -> None:
    if (
        feedback.target.type == FeedbackTargetType.RUN
        and feedback.target.id != experience.execution_id
    ):
        raise LearningScopeError("feedback target does not match the experience")
    context = feedback.execution_context
    if context is not None:
        if context.tenant_id not in (None, experience.scope.tenant_id):
            raise LearningScopeError("feedback tenant does not match the experience")
        if context.run_id not in (None, experience.execution_id):
            raise LearningScopeError("feedback execution does not match the experience")
        if experience.scope.visibility is ScopeVisibility.AGENT and context.agent_id not in (
            None,
            experience.scope.agent_id,
        ):
            raise LearningScopeError("feedback agent does not match the experience")


__all__ = [
    "AssessmentEvidence",
    "AssessmentStatus",
    "ConservativeFeedbackCritic",
    "FeedbackAssessment",
    "FeedbackCritic",
    "LearningDecision",
]
