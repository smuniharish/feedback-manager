from uuid import uuid4

import pytest
from pydantic import ValidationError

from feedback_manager.learning import (
    ArtifactScope,
    ArtifactStatus,
    ArtifactType,
    Evidence,
    LearningSignal,
    LearningValidationError,
    SignalType,
)
from feedback_manager.learning.learners import (
    ConstraintLearner,
    KnowledgeLearner,
    OutcomePatternLearner,
    StrategyLearner,
)
from feedback_manager.learning.patterns import PatternGroup, PatternMiner


def signal(
    content: str,
    *,
    kind: SignalType = SignalType.CORRECTION,
    tenant: str = "tenant",
    agent: str = "agent",
    run: str = "run",
) -> LearningSignal:
    return LearningSignal(
        kind=kind,
        scope=ArtifactScope(tenant_id=tenant, agent_id=agent),
        content=content,
        evidence=Evidence(
            feedback_id=uuid4(), execution_id=run, source="human", independence_key=run
        ),
    )


def test_miner_groups_deterministically_without_duplicate_or_cross_tenant_evidence() -> None:
    first = signal("Validate   before execution", run="run-1")
    second = signal("Validate before execution", run="run-2")
    foreign = signal("Validate before execution", tenant="other", run="run-3")
    miner = PatternMiner()
    patterns = miner.mine([first, second, first, foreign])
    assert sorted(len(item.signals) for item in patterns) == [1, 2]
    grouped = next(item for item in patterns if len(item.signals) == 2)
    assert grouped.scope.tenant_id == "tenant"
    assert miner.mine([second, foreign, first]) == patterns
    with pytest.raises(LearningValidationError, match="conflicting content"):
        miner.mine([first, first.model_copy(update={"content": "different"})])


def test_miner_suggests_similar_patterns_without_merging_evidence() -> None:
    miner = PatternMiner()
    patterns = miner.mine(
        [
            signal("search validate results"),
            signal("search validate results carefully", run="run-2"),
            signal("search validate results carefully", agent="other", run="run-3"),
        ]
    )
    suggestions = miner.similar(patterns, threshold=0.75)
    assert len(suggestions) == 1
    assert suggestions[0].score == pytest.approx(0.75)
    assert all(len(item.signals) == 1 for item in patterns)
    with pytest.raises(LearningValidationError, match="threshold"):
        miner.similar(patterns, threshold=0)
    with pytest.raises(LearningValidationError, match="limit"):
        miner.similar(patterns, limit=0)
    with pytest.raises(LearningValidationError, match="distinct patterns"):
        miner.similar([patterns[0], patterns[0]])
    assert not miner.similar(patterns, threshold=1)
    with pytest.raises(LearningValidationError, match="limited to 200"):
        miner.similar([patterns[0]] * 201)


def test_pattern_validation_and_batch_limits_are_explicit() -> None:
    item = signal("A correction")
    with pytest.raises(LearningValidationError, match="positive integer"):
        PatternMiner(max_signals=0)
    with pytest.raises(LearningValidationError, match="maximum"):
        PatternMiner(max_signals=1).mine([item, signal("other")])
    group = PatternMiner().mine([item])[0]
    data = group.model_dump()
    data["pattern_id"] = "0" * 64
    with pytest.raises(ValidationError, match="pattern ID"):
        PatternGroup.model_validate(data)
    data = group.model_dump()
    data["signals"] = [item, item]
    with pytest.raises(ValidationError, match="signal IDs must be unique"):
        PatternGroup.model_validate(data)
    with pytest.raises(ValidationError, match="matching scope"):
        PatternGroup.model_validate({**group.model_dump(), "signals": [signal("other")]})
    blank = item.model_copy(update={"content": " "})
    with pytest.raises(LearningValidationError, match="must not be blank"):
        PatternMiner().mine([blank])


@pytest.mark.parametrize(
    ("kind", "learner", "artifact_type"),
    [
        (SignalType.CORRECTION, KnowledgeLearner(), ArtifactType.KNOWLEDGE),
        (SignalType.IMPROVEMENT, StrategyLearner(), ArtifactType.STRATEGY),
        (SignalType.CONSTRAINT, ConstraintLearner(), ArtifactType.CONSTRAINT),
        (SignalType.FAILURE, OutcomePatternLearner(), ArtifactType.PATTERN),
        (SignalType.SUCCESS, OutcomePatternLearner(), ArtifactType.PATTERN),
    ],
)
def test_learners_create_reviewable_candidates_from_explicit_evidence(
    kind: SignalType,
    learner: KnowledgeLearner | StrategyLearner | ConstraintLearner | OutcomePatternLearner,
    artifact_type: ArtifactType,
) -> None:
    group = PatternMiner().mine(
        [signal("Validated lesson", kind=kind, run="run-1"), signal("Validated lesson", kind=kind)]
    )[0]
    candidate = learner.learn(group)
    assert candidate.artifact_type is artifact_type
    assert candidate.status is ArtifactStatus.CANDIDATE
    assert candidate.evidence_count == 2
    assert len(candidate.source_feedback_ids) == 2
    assert candidate.artifact_id == learner.learn(group).artifact_id


def test_learners_reject_incompatible_or_forged_patterns() -> None:
    correction = PatternMiner().mine([signal("Correction")])[0]
    for learner in (StrategyLearner(), ConstraintLearner(), OutcomePatternLearner()):
        with pytest.raises(LearningValidationError):
            learner.learn(correction)
    improvement = PatternMiner().mine([signal("Improve", kind=SignalType.IMPROVEMENT)])[0]
    with pytest.raises(LearningValidationError):
        KnowledgeLearner().learn(improvement)
    forged = correction.model_copy(update={"scope": ArtifactScope(tenant_id="other", agent_id="a")})
    with pytest.raises(ValidationError, match="pattern ID"):
        KnowledgeLearner().learn(forged)
