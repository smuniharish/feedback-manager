from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from feedback_manager.learning import (
    ArtifactScope,
    ArtifactType,
    Evidence,
    LearnedArtifact,
    LearningValidationError,
    ScopeVisibility,
    ValidationRecord,
)
from feedback_manager.learning.confidence import ConfidenceConfig, ConfidenceEngine
from feedback_manager.learning.validation import (
    DefaultLearningValidationPolicy,
    LearningValidationConfig,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def evidence(
    key: str,
    *,
    contradicts: bool = False,
    reviewed: bool = False,
    quality: float = 1.0,
    reliability: float = 1.0,
    age_days: int = 0,
) -> Evidence:
    return Evidence(
        feedback_id=uuid4(),
        source="human",
        independence_key=key,
        contradicts=contradicts,
        human_reviewed=reviewed,
        quality=quality,
        source_reliability=reliability,
        observed_at=NOW - timedelta(days=age_days),
    )


def artifact(*items: Evidence, shared: bool = False) -> LearnedArtifact:
    scope = (
        ArtifactScope(tenant_id="tenant", visibility=ScopeVisibility.TENANT)
        if shared
        else ArtifactScope(tenant_id="tenant", agent_id="agent")
    )
    return LearnedArtifact(
        artifact_type=ArtifactType.KNOWLEDGE,
        title="Correction",
        content="Verified content",
        scope=scope,
        evidence=items,
    )


def approve(item: LearnedArtifact) -> LearnedArtifact:
    record = ValidationRecord(
        reviewer="reviewer",
        approved=True,
        policy_version="default-v1",
        reviewed_version=item.version,
        artifact_digest=item.review_digest(),
        occurred_at=NOW,
    )
    return item.model_copy(update={"validation_history": (record,)})


def test_default_confidence_is_explainable_and_deduplicates_sources() -> None:
    engine = ConfidenceEngine()
    empty = engine.score([], as_of=NOW)
    assert empty.confidence == 0
    one = engine.score([evidence("run-1")], as_of=NOW)
    assert one.independent_support == 1
    assert one.confidence == pytest.approx(0.7)
    two = engine.score([evidence("run-1"), evidence("run-2")], as_of=NOW)
    assert two.confidence == pytest.approx(0.9)
    duplicate = engine.score(
        [evidence("run-1", quality=0.4), evidence("run-1", reviewed=True)], as_of=NOW
    )
    assert duplicate.independent_support == 1
    assert duplicate.quality == 1
    assert duplicate.confidence == pytest.approx(0.8)
    opposed = engine.score([evidence("run-1"), evidence("run-2", contradicts=True)], as_of=NOW)
    assert opposed.independent_contradictions == 1
    assert opposed.agreement == pytest.approx(0.5)
    assert opposed.confidence == pytest.approx(0.6)


def test_confidence_supports_configurable_recency_and_reliability() -> None:
    engine = ConfidenceEngine(ConfidenceConfig(half_life_days=10))
    result = engine.score([evidence("run", age_days=10, reliability=0.8)], as_of=NOW)
    assert result.quality == pytest.approx(0.4)
    assert result.confidence == pytest.approx(0.52)
    with pytest.raises(ValidationError, match="weights must sum"):
        ConfidenceConfig(support_weight=0.1)
    with pytest.raises(LearningValidationError, match="timezone-aware"):
        engine.score([], as_of=NOW.replace(tzinfo=None))
    with pytest.raises(LearningValidationError, match="future"):
        engine.score([evidence("future", age_days=-1)], as_of=NOW)


def test_policy_requires_independent_evidence_and_separate_human_review() -> None:
    policy = DefaultLearningValidationPolicy()
    one = artifact(evidence("run-1"))
    decision = policy.evaluate(one, as_of=NOW)
    assert not decision.eligible
    assert "insufficient independent supporting evidence" in decision.reasons
    assert "an approved human review is required" in decision.reasons
    two = artifact(evidence("run-1"), evidence("run-2"))
    assert not policy.evaluate(two, as_of=NOW).eligible
    reviewed = approve(two)
    assert policy.evaluate(reviewed, as_of=NOW).eligible
    modified = reviewed.model_copy(update={"content": "changed"})
    assert not policy.evaluate(modified, as_of=NOW).eligible


def test_policy_rejects_insufficient_confidence() -> None:
    decision = DefaultLearningValidationPolicy().evaluate(
        artifact(evidence("run", quality=0.1)), as_of=NOW
    )
    assert "confidence is below the configured minimum" in decision.reasons


def test_review_is_bound_to_the_validation_policy_version() -> None:
    reviewed = approve(artifact(evidence("run-1"), evidence("run-2")))
    policy = DefaultLearningValidationPolicy(LearningValidationConfig(version="new-policy"))
    decision = policy.evaluate(reviewed, as_of=NOW)
    assert not decision.eligible
    assert "an approved human review is required" in decision.reasons


def test_invalidated_evidence_remains_auditable_but_does_not_count() -> None:
    invalid = evidence("run-1").model_copy(update={"valid": False})
    valid = evidence("run-2")
    item = artifact(invalid, valid)
    restored = LearnedArtifact.model_validate_json(item.model_dump_json())
    assert restored.evidence_count == 1
    assert set(restored.source_feedback_ids) == {invalid.feedback_id, valid.feedback_id}
    decision = DefaultLearningValidationPolicy().evaluate(restored, as_of=NOW)
    assert decision.excluded_evidence_count == 1
    assert decision.confidence.independent_support == 1
    assert not decision.eligible


def test_policy_configures_thresholds_contradictions_source_quality_and_age() -> None:
    config = LearningValidationConfig(
        min_independent_evidence=1,
        min_confidence=0,
        min_source_reliability=0.5,
        max_evidence_age_days=30,
        minimum_by_type={ArtifactType.KNOWLEDGE: 2},
        minimum_by_visibility={ScopeVisibility.TENANT: 3},
    )
    policy = DefaultLearningValidationPolicy(config)
    item = artifact(
        evidence("good"),
        evidence("unreliable", reliability=0.1),
        evidence("old", age_days=31),
        evidence("contradiction", contradicts=True),
        shared=True,
    )
    result = policy.evaluate(item, as_of=NOW)
    assert result.required_independent_evidence == 3
    assert result.excluded_evidence_count == 2
    assert "contradictory evidence exceeds the configured limit" in result.reasons
    with pytest.raises(ValidationError):
        LearningValidationConfig.model_validate({"minimum_by_type": {"knowledge": 0}})


def test_single_unreviewed_item_cannot_activate_shared_knowledge() -> None:
    policy = DefaultLearningValidationPolicy(
        LearningValidationConfig(
            min_independent_evidence=1, min_confidence=0, require_manual_review=False
        )
    )
    item = artifact(evidence("run"), shared=True)
    result = policy.evaluate(item, as_of=NOW)
    assert "one unreviewed item cannot activate tenant-wide knowledge" in result.reasons
    assert policy.evaluate(approve(item), as_of=NOW).eligible


def test_policy_rejects_invalid_evaluation_timestamps() -> None:
    policy = DefaultLearningValidationPolicy()
    with pytest.raises(LearningValidationError, match="timezone-aware"):
        policy.evaluate(artifact(), as_of=NOW.replace(tzinfo=None))
    with pytest.raises(LearningValidationError, match="future"):
        policy.evaluate(artifact(evidence("future", age_days=-1)), as_of=NOW)
    reviewed = approve(artifact(evidence("run", age_days=1)))
    with pytest.raises(LearningValidationError, match="review cannot occur in the future"):
        policy.evaluate(reviewed, as_of=NOW - timedelta(seconds=1))
