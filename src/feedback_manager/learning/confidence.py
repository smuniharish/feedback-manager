"""Explainable confidence scoring based on independent evidence."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

from feedback_manager.learning.errors import LearningValidationError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from feedback_manager.learning.models import Evidence


class ConfidenceConfig(BaseModel):
    """Weights and optional age decay used by `ConfidenceEngine`."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    independent_target: int = Field(default=2, ge=1)
    support_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    quality_weight: float = Field(default=0.3, ge=0.0, le=1.0)
    agreement_weight: float = Field(default=0.2, ge=0.0, le=1.0)
    review_weight: float = Field(default=0.1, ge=0.0, le=1.0)
    half_life_days: float | None = Field(default=None, gt=0.0)

    @model_validator(mode="after")
    def _validate_weights(self) -> ConfidenceConfig:
        total = (
            self.support_weight + self.quality_weight + self.agreement_weight + self.review_weight
        )
        if not math.isclose(total, 1.0, abs_tol=1e-9):
            raise ValueError("confidence weights must sum to 1")
        return self


class ConfidenceBreakdown(BaseModel):
    """A confidence score and the evidence-derived factors behind it."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    independent_support: int = Field(ge=0)
    independent_contradictions: int = Field(ge=0)
    support: float = Field(ge=0.0, le=1.0)
    quality: float = Field(ge=0.0, le=1.0)
    agreement: float = Field(ge=0.0, le=1.0)
    review: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)


class ConfidenceEngine:
    """Score evidence without using vector similarity.

    Support is capped at ``independent_target``. Quality is the mean of the
    best quality * reliability * age-decay value per independent source.
    Agreement is support / (support + contradictions), and review is the
    fraction of independent supporting sources marked human-reviewed.
    """

    def __init__(self, config: ConfidenceConfig | None = None) -> None:
        self.config = config if config is not None else ConfidenceConfig()

    def score(
        self, evidence: Sequence[Evidence], *, as_of: datetime | None = None
    ) -> ConfidenceBreakdown:
        """Return the deterministic score and its component factors.

        ``as_of`` must be timezone-aware and cannot precede an evidence item.
        """
        now = datetime.now(UTC) if as_of is None else as_of
        if now.utcoffset() is None:
            raise LearningValidationError("confidence evaluation time must be timezone-aware")
        supporting: dict[str, tuple[float, bool]] = {}
        contradictory: set[str] = set()
        for item in evidence:
            if not item.valid:
                continue
            age_seconds = (now - item.observed_at).total_seconds()
            if age_seconds < 0:
                raise LearningValidationError("evidence cannot be observed in the future")
            if item.contradicts:
                contradictory.add(item.independence_key)
                continue
            decay = (
                1.0
                if self.config.half_life_days is None
                else 2 ** (-age_seconds / (self.config.half_life_days * 86400))
            )
            quality = item.quality * item.source_reliability * decay
            previous_quality, previous_review = supporting.get(item.independence_key, (0.0, False))
            supporting[item.independence_key] = (
                max(previous_quality, quality),
                previous_review or item.human_reviewed,
            )

        count = len(supporting)
        if count == 0:
            return ConfidenceBreakdown(
                independent_support=0,
                independent_contradictions=len(contradictory),
                support=0.0,
                quality=0.0,
                agreement=0.0,
                review=0.0,
                confidence=0.0,
            )
        support = min(count / self.config.independent_target, 1.0)
        quality = sum(value[0] for value in supporting.values()) / count
        agreement = count / (count + len(contradictory))
        review = sum(value[1] for value in supporting.values()) / count
        confidence = (
            self.config.support_weight * support
            + self.config.quality_weight * quality
            + self.config.agreement_weight * agreement
            + self.config.review_weight * review
        )
        return ConfidenceBreakdown(
            independent_support=count,
            independent_contradictions=len(contradictory),
            support=support,
            quality=quality,
            agreement=agreement,
            review=review,
            confidence=min(confidence, 1.0),
        )


__all__ = ["ConfidenceBreakdown", "ConfidenceConfig", "ConfidenceEngine"]
