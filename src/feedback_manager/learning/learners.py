"""Build candidate artifacts from explicit, grouped learning signals."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import NAMESPACE_URL, uuid5

from feedback_manager.learning.confidence import ConfidenceEngine
from feedback_manager.learning.errors import LearningValidationError
from feedback_manager.learning.models import ArtifactType, LearnedArtifact, SignalType
from feedback_manager.learning.patterns import PatternGroup


class ArtifactLearner(ABC):
    """Convert a supported pattern into an untrusted candidate artifact."""

    @abstractmethod
    def learn(self, pattern: PatternGroup) -> LearnedArtifact:
        """Build a candidate or raise when the pattern is unsupported."""


def _candidate(
    pattern: PatternGroup,
    *,
    artifact_type: ArtifactType,
    title_prefix: str,
    confidence: ConfidenceEngine,
) -> LearnedArtifact:
    pattern = PatternGroup.model_validate(pattern.model_dump())
    content = pattern.signals[0].content
    evidence = tuple(signal.evidence for signal in pattern.signals)
    return LearnedArtifact(
        artifact_id=uuid5(NAMESPACE_URL, f"feedback-manager:artifact:{pattern.pattern_id}"),
        artifact_type=artifact_type,
        title=f"{title_prefix}: {content[:450]}",
        description="Candidate derived from explicitly reported learning signals.",
        content=content,
        scope=pattern.scope,
        evidence=evidence,
        confidence=confidence.score(evidence).confidence,
        provenance={
            "pattern_id": pattern.pattern_id,
            "signal_ids": [str(signal.signal_id) for signal in pattern.signals],
        },
    )


class KnowledgeLearner(ArtifactLearner):
    """Create knowledge candidates from explicit corrections."""

    def __init__(self, confidence: ConfidenceEngine | None = None) -> None:
        self.confidence = confidence if confidence is not None else ConfidenceEngine()

    def learn(self, pattern: PatternGroup) -> LearnedArtifact:
        """Create a candidate from correction signals."""
        if pattern.kind is not SignalType.CORRECTION:
            raise LearningValidationError("knowledge learning requires correction signals")
        return _candidate(
            pattern,
            artifact_type=ArtifactType.KNOWLEDGE,
            title_prefix="Correction",
            confidence=self.confidence,
        )


class StrategyLearner(ArtifactLearner):
    """Create strategy candidates from explicit human-provided improvements."""

    def __init__(self, confidence: ConfidenceEngine | None = None) -> None:
        self.confidence = confidence if confidence is not None else ConfidenceEngine()

    def learn(self, pattern: PatternGroup) -> LearnedArtifact:
        """Create a candidate from improvement signals."""
        if pattern.kind is not SignalType.IMPROVEMENT:
            raise LearningValidationError("strategy learning requires improvement signals")
        return _candidate(
            pattern,
            artifact_type=ArtifactType.STRATEGY,
            title_prefix="Improvement",
            confidence=self.confidence,
        )


class ConstraintLearner(ArtifactLearner):
    """Create contextual constraint candidates from explicit human observations."""

    def __init__(self, confidence: ConfidenceEngine | None = None) -> None:
        self.confidence = confidence if confidence is not None else ConfidenceEngine()

    def learn(self, pattern: PatternGroup) -> LearnedArtifact:
        """Create a candidate from constraint signals."""
        if pattern.kind is not SignalType.CONSTRAINT:
            raise LearningValidationError("constraint learning requires constraint signals")
        return _candidate(
            pattern,
            artifact_type=ArtifactType.CONSTRAINT,
            title_prefix="Constraint",
            confidence=self.confidence,
        )


class OutcomePatternLearner(ArtifactLearner):
    """Create pattern candidates from reported successes or failures."""

    def __init__(self, confidence: ConfidenceEngine | None = None) -> None:
        self.confidence = confidence if confidence is not None else ConfidenceEngine()

    def learn(self, pattern: PatternGroup) -> LearnedArtifact:
        """Create a candidate from successful or failed outcomes."""
        if pattern.kind not in (SignalType.SUCCESS, SignalType.FAILURE):
            raise LearningValidationError("outcome patterns require success or failure signals")
        return _candidate(
            pattern,
            artifact_type=ArtifactType.PATTERN,
            title_prefix="Outcome pattern",
            confidence=self.confidence,
        )


__all__ = [
    "ArtifactLearner",
    "ConstraintLearner",
    "KnowledgeLearner",
    "OutcomePatternLearner",
    "StrategyLearner",
]
