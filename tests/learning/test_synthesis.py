import asyncio
import itertools
from unittest.mock import create_autospec
from uuid import uuid4

import pytest

from feedback_manager.learning import (
    ArtifactScope,
    ArtifactStatus,
    ArtifactType,
    Evidence,
    LearningSignal,
    LearningValidationError,
    SignalType,
    TenantScope,
)
from feedback_manager.learning.assessment_verification import (
    AssessmentVerification,
    AssessmentVerifier,
)
from feedback_manager.learning.engine import LearningEngine
from feedback_manager.learning.patterns import PatternMiner, PatternSimilarity
from feedback_manager.learning.synthesis import (
    DeterministicSynthesizer,
    ModelSynthesizer,
    PatternSupport,
    SynthesisDraft,
    SynthesisInput,
    SynthesisModel,
    cluster_patterns,
    validate_draft,
)
from feedback_manager.storage.artifacts import InMemoryArtifactRepository
from feedback_manager.storage.artifacts.base import ArtifactQuery

SCOPE = ArtifactScope(tenant_id="tenant", agent_id="agent")


def signal(
    content: str,
    run: str,
    *,
    kind: SignalType = SignalType.IMPROVEMENT,
    contradicts: bool = False,
    scope: ArtifactScope = SCOPE,
    feedback: bool = True,
) -> LearningSignal:
    return LearningSignal(
        kind=kind,
        scope=scope,
        content=content,
        evidence=Evidence(
            feedback_id=uuid4() if feedback else None,
            source="human",
            independence_key=run,
            execution_id=run,
            contradicts=contradicts,
        ),
    )


def engine(*, eligible: bool = True) -> tuple[LearningEngine, InMemoryArtifactRepository]:
    verifier = create_autospec(AssessmentVerifier, instance=True)
    verdict = AssessmentVerification(eligible=eligible, reason="test-double")
    verifier.verify_signal.return_value = verdict
    verifier.verify_artifact.return_value = verdict
    repository = InMemoryArtifactRepository()
    return LearningEngine(repository, assessment_verifier=verifier), repository


RELATED = (
    signal("validate the ledger before posting", "a"),
    signal("validate the ledger before posting", "b"),
    signal("validate the ledger before posting entries", "c"),
    signal("validate the ledger before posting entries", "d", contradicts=True),
)


def request_for(signals: tuple[LearningSignal, ...]) -> SynthesisInput:
    patterns = PatternMiner().mine(signals)
    return SynthesisInput(
        scope=SCOPE,
        kind=SignalType.IMPROVEMENT,
        patterns=tuple(PatternSupport.of(pattern) for pattern in patterns),
    )


async def test_engine_synthesizes_a_grounded_candidate_without_widening_scope() -> None:
    learning, repository = engine()
    unrelated = signal("rotate credentials quarterly", "e")
    result = await learning.synthesize((*RELATED, unrelated), threshold=0.7)

    (artifact,) = result.artifacts
    assert artifact.status is ArtifactStatus.CANDIDATE
    assert artifact.artifact_type is ArtifactType.STRATEGY
    assert artifact.scope == SCOPE
    assert "[support: 2; contradictions: 0]" in artifact.content
    assert "[support: 1; contradictions: 1]" in artifact.content
    assert "rotate credentials" not in artifact.content
    assert artifact.provenance["synthesizer"] == "deterministic-v1"
    pattern_ids = artifact.provenance["pattern_ids"]
    contradicting = artifact.provenance["contradicting_signal_ids"]
    assert isinstance(pattern_ids, list)
    assert len(pattern_ids) == 2
    assert isinstance(contradicting, list)
    assert len(contradicting) == 1
    assert artifact.contradictory_evidence_count == 1
    assert await repository.get(SCOPE, artifact.artifact_id) is not None

    again = await learning.synthesize((*RELATED, unrelated), threshold=0.7)
    assert again.artifacts[0].artifact_id == artifact.artifact_id


async def test_engine_synthesis_requires_eligible_feedback() -> None:
    learning, _ = engine(eligible=False)
    with pytest.raises(LearningValidationError, match="verification failed"):
        await learning.synthesize(RELATED, threshold=0.7)


async def test_engine_synthesis_reports_unsupported_kinds_and_uses_given_similarities() -> None:
    learning, _ = engine()
    approvals = (
        signal("approved ledger flow", "a", kind=SignalType.APPROVAL),
        signal("approved ledger flow now", "b", kind=SignalType.APPROVAL),
    )
    left, right = PatternMiner().mine(approvals)
    similarity = PatternSimilarity(
        left_pattern_id=left.pattern_id, right_pattern_id=right.pattern_id, score=0.5
    )
    result = await learning.synthesize(approvals, similarities=(similarity,))
    assert result.artifacts == ()
    assert set(result.unsupported_signal_ids) == {item.signal_id for item in approvals}


async def test_deterministic_synthesizer_excludes_contradicted_only_patterns() -> None:
    signals = (
        signal("use the staging ledger", "a"),
        signal("use the staging ledger now", "b", contradicts=True),
    )
    request = request_for(signals)
    draft = await DeterministicSynthesizer().synthesize(request)
    assert len(draft.cited_pattern_ids) == 1
    assert "Excluded contradicted reports: 1" in draft.content
    assert "staging ledger now" not in draft.content
    assert validate_draft(request, draft)[0].independent_support == 1

    contradicted = request_for(
        (
            signal("use the staging ledger", "a", contradicts=True),
            signal("use the staging ledger now", "b", contradicts=True),
        )
    )
    with pytest.raises(LearningValidationError, match="independently supported"):
        await DeterministicSynthesizer().synthesize(contradicted)


async def test_deterministic_synthesizer_respects_the_content_bound() -> None:
    long_text = "x" * 9_990
    request = request_for((signal(long_text, "a"), signal(long_text + " y", "b")))
    with pytest.raises(LearningValidationError, match="content bound"):
        await DeterministicSynthesizer().synthesize(request)

    fitting = "y" * 9_900
    request = request_for(
        (
            signal(fitting, "a"),
            signal(fitting, "c"),
            signal(fitting + " z", "b"),
            signal(fitting + " w", "d", contradicts=True),
        )
    )
    draft = await DeterministicSynthesizer().synthesize(request)
    assert len(draft.cited_pattern_ids) == 1
    assert len(draft.content) <= 10_000
    assert "Excluded" not in draft.content


class StubModel(SynthesisModel):
    def __init__(self, cited: tuple[str, ...], *, delay: float = 0.0) -> None:
        self.cited = cited
        self.delay = delay

    async def generate(self, request: SynthesisInput) -> SynthesisDraft:
        await asyncio.sleep(self.delay)
        return SynthesisDraft(title="Model", content="grounded", cited_pattern_ids=self.cited)


async def test_model_synthesizer_validates_citations_and_time() -> None:
    request = request_for(RELATED)
    ids = tuple(item.pattern.pattern_id for item in request.patterns)
    synthesizer = ModelSynthesizer(StubModel(ids), name="stub")
    draft = await synthesizer.synthesize(request)
    assert synthesizer.name == "stub"
    assert len(validate_draft(request, draft)) == 2

    for cited, message in (
        (("0" * 64,), "unknown"),
        ((ids[0], ids[0]), "more than once"),
    ):
        bad = await ModelSynthesizer(StubModel(cited), name="stub").synthesize(request)
        with pytest.raises(LearningValidationError, match=message):
            validate_draft(request, bad)

    contradicted = request_for(
        (signal("avoid retries", "a"), signal("avoid retries now", "b", contradicts=True))
    )
    unsupported = next(
        item.pattern.pattern_id for item in contradicted.patterns if not item.independent_support
    )
    draft = SynthesisDraft(title="t", content="c", cited_pattern_ids=(unsupported,))
    with pytest.raises(LearningValidationError, match="unsupported"):
        validate_draft(contradicted, draft)

    slow = ModelSynthesizer(StubModel(ids, delay=1), name="slow", timeout=0.01)
    with pytest.raises(LearningValidationError, match="timed out"):
        await slow.synthesize(request)
    with pytest.raises(ValueError, match="timeout"):
        ModelSynthesizer(StubModel(ids), name="bad", timeout=0)


async def test_engine_rejects_ungrounded_model_drafts() -> None:
    learning, repository = engine()
    synthesizer = ModelSynthesizer(StubModel(("0" * 64,)), name="stub")
    with pytest.raises(LearningValidationError, match="unknown"):
        await learning.synthesize(RELATED, synthesizer=synthesizer, threshold=0.7)
    assert (await repository.query(_query())).artifacts == ()


def _query() -> ArtifactQuery:
    return ArtifactQuery(scope=TenantScope(tenant_id="tenant"))


def test_cluster_inputs_are_bounded_and_scoped() -> None:
    other = ArtifactScope(tenant_id="tenant", agent_id="other")
    patterns = PatternMiner().mine(
        (signal("alpha", "a"), signal("alpha beta", "b"), signal("alpha", "c", scope=other))
    )
    mine = [item for item in patterns if item.scope == SCOPE]
    foreign = next(item for item in patterns if item.scope == other)
    with pytest.raises(LearningValidationError, match="share scope"):
        cluster_patterns(
            patterns,
            (
                PatternSimilarity(
                    left_pattern_id=mine[0].pattern_id,
                    right_pattern_id=foreign.pattern_id,
                    score=1,
                ),
            ),
        )
    with pytest.raises(LearningValidationError, match="unknown pattern"):
        cluster_patterns(
            mine,
            (
                PatternSimilarity(
                    left_pattern_id=mine[0].pattern_id, right_pattern_id="f" * 64, score=1
                ),
            ),
        )
    with pytest.raises(LearningValidationError, match="distinct"):
        cluster_patterns((mine[0], mine[0]), ())
    assert cluster_patterns(patterns, ()) == ()

    many = PatternMiner().mine(tuple(signal(f"item {index}", str(index)) for index in range(51)))
    chain = tuple(
        PatternSimilarity(left_pattern_id=a.pattern_id, right_pattern_id=b.pattern_id, score=1)
        for a, b in itertools.pairwise(many)
    )
    with pytest.raises(LearningValidationError, match="exceeds 50"):
        cluster_patterns(many, chain)


def test_synthesis_input_rejects_mixed_or_duplicate_patterns() -> None:
    patterns = PatternMiner().mine(
        (signal("alpha", "a"), signal("alpha", "b", kind=SignalType.CONSTRAINT))
    )
    supports = tuple(PatternSupport.of(pattern) for pattern in patterns)
    with pytest.raises(ValueError, match="share scope and kind"):
        SynthesisInput(scope=SCOPE, kind=SignalType.IMPROVEMENT, patterns=supports)
    with pytest.raises(ValueError, match="distinct"):
        SynthesisInput(
            scope=SCOPE, kind=supports[0].pattern.kind, patterns=(supports[0], supports[0])
        )
