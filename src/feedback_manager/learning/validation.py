"""Configurable validation of learned artifacts and their evidence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field

from feedback_manager.core._types import Identifier
from feedback_manager.learning.confidence import ConfidenceBreakdown, ConfidenceEngine
from feedback_manager.learning.errors import LearningValidationError
from feedback_manager.learning.models import ArtifactType, ScopeVisibility

if TYPE_CHECKING:
    from feedback_manager.learning.models import LearnedArtifact


class LearningValidationConfig(BaseModel):
    """Evidence and review requirements for artifact publication."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    version: Identifier = "default-v1"
    min_independent_evidence: int = Field(default=2, ge=1)
    min_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    max_contradictions: int = Field(default=0, ge=0)
    min_source_reliability: float = Field(default=0.0, ge=0.0, le=1.0)
    max_evidence_age_days: float | None = Field(default=None, gt=0.0)
    require_manual_review: bool = True
    minimum_by_type: dict[ArtifactType, Annotated[int, Field(ge=1)]] = Field(default_factory=dict)
    minimum_by_visibility: dict[ScopeVisibility, Annotated[int, Field(ge=1)]] = Field(
        default_factory=dict
    )


class ValidationDecision(BaseModel):
    """An explainable policy decision, independent of feedback approval."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    eligible: bool
    reasons: tuple[str, ...]
    required_independent_evidence: int = Field(ge=1)
    excluded_evidence_count: int = Field(ge=0)
    confidence: ConfidenceBreakdown
    policy_version: Identifier


class LearningValidationPolicy(ABC):
    """An application-replaceable artifact validation policy."""

    @abstractmethod
    def evaluate(
        self, artifact: LearnedArtifact, *, as_of: datetime | None = None
    ) -> ValidationDecision:
        """Evaluate the artifact without changing or publishing it."""


class DefaultLearningValidationPolicy(LearningValidationPolicy):
    """Apply configurable evidence thresholds and separate human review."""

    def __init__(
        self,
        config: LearningValidationConfig | None = None,
        confidence_engine: ConfidenceEngine | None = None,
    ) -> None:
        self.config = config if config is not None else LearningValidationConfig()
        self.confidence_engine = (
            confidence_engine if confidence_engine is not None else ConfidenceEngine()
        )

    def evaluate(
        self, artifact: LearnedArtifact, *, as_of: datetime | None = None
    ) -> ValidationDecision:
        """Evaluate evidence, confidence, contradictions and reviewer decision."""
        now = datetime.now(UTC) if as_of is None else as_of
        if now.utcoffset() is None:
            raise LearningValidationError("validation time must be timezone-aware")
        for item in artifact.evidence:
            if item.observed_at > now:
                raise LearningValidationError("evidence cannot be observed in the future")
        cutoff = (
            None
            if self.config.max_evidence_age_days is None
            else now - timedelta(days=self.config.max_evidence_age_days)
        )
        accepted = tuple(
            item
            for item in artifact.evidence
            if item.valid
            and item.source_reliability >= self.config.min_source_reliability
            and (cutoff is None or item.observed_at >= cutoff)
        )
        score = self.confidence_engine.score(accepted, as_of=now)
        minimum = max(
            self.config.min_independent_evidence,
            self.config.minimum_by_type.get(artifact.artifact_type, 1),
            self.config.minimum_by_visibility.get(artifact.scope.visibility, 1),
        )
        review = artifact.validation_history[-1] if artifact.validation_history else None
        if review is not None and review.occurred_at > now:
            raise LearningValidationError("artifact review cannot occur in the future")
        approved = bool(
            review
            and review.approved
            and review.policy_version == self.config.version
            and review.reviewed_version <= artifact.version
            and review.artifact_digest == artifact.review_digest()
        )
        reasons: list[str] = []
        if score.independent_support < minimum:
            reasons.append("insufficient independent supporting evidence")
        if score.independent_contradictions > self.config.max_contradictions:
            reasons.append("contradictory evidence exceeds the configured limit")
        if score.confidence < self.config.min_confidence:
            reasons.append("confidence is below the configured minimum")
        if self.config.require_manual_review and not approved:
            reasons.append("an approved human review is required")
        if (
            artifact.scope.visibility is ScopeVisibility.TENANT
            and score.independent_support < 2
            and not approved
        ):
            reasons.append("one unreviewed item cannot activate tenant-wide knowledge")
        return ValidationDecision(
            eligible=not reasons,
            reasons=tuple(reasons),
            required_independent_evidence=minimum,
            excluded_evidence_count=len(artifact.evidence) - len(accepted),
            confidence=score,
            policy_version=self.config.version,
        )


__all__ = [
    "DefaultLearningValidationPolicy",
    "LearningValidationConfig",
    "LearningValidationPolicy",
    "ValidationDecision",
]
