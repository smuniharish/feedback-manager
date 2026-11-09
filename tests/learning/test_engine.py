import asyncio
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest

from feedback_manager.learning import (
    ArtifactConflictError,
    ArtifactScope,
    ArtifactStatus,
    Evidence,
    LearningScopeError,
    LearningSignal,
    LearningValidationError,
    SignalType,
    TenantScope,
    ValidationRecord,
)
from feedback_manager.learning.engine import LearningEngine
from feedback_manager.learning.validation import (
    DefaultLearningValidationPolicy,
    LearningValidationConfig,
)
from feedback_manager.retrieval import RetrievalRequest
from feedback_manager.storage.artifacts import ArtifactPage, InMemoryArtifactRepository


def signal(kind: SignalType = SignalType.IMPROVEMENT, *, run: str = "run") -> LearningSignal:
    return LearningSignal(
        kind=kind,
        scope=ArtifactScope(tenant_id="tenant", agent_id="agent"),
        content="Search, validate, then execute",
        evidence=Evidence(
            feedback_id=uuid4(), source="human", independence_key=run, execution_id=run
        ),
    )


async def test_incremental_processing_is_idempotent_and_requires_review() -> None:
    repository = InMemoryArtifactRepository()
    engine = LearningEngine(repository)
    first, second = signal(run="run-1"), signal(run="run-2")
    candidate = (await engine.process([first])).artifacts[0]
    assert candidate.status is ArtifactStatus.CANDIDATE
    assert candidate.evidence_count == 1
    assert (await engine.process([first])).artifacts[0].version == 1
    updated = (await engine.process([second])).artifacts[0]
    assert updated.version == 2
    assert updated.evidence_count == 2
    assert updated.confidence == pytest.approx(0.9)
    stored_ids = updated.provenance["signal_ids"]
    assert isinstance(stored_ids, list)
    assert stored_ids == sorted([str(first.signal_id), str(second.signal_id)])
    assert (await engine.process([first, second])).artifacts[0].version == 2
    result = await engine.retrieve(RetrievalRequest(scope=first.scope, agent_id="agent"))
    assert not result.artifacts
    unsupported = signal(SignalType.APPROVAL)
    result = await engine.process([unsupported])
    assert result.unsupported_signal_ids == (unsupported.signal_id,)


async def test_manual_review_and_activation_enforce_policy_and_scope() -> None:
    repository = InMemoryArtifactRepository()
    engine = LearningEngine(repository)
    first, second = signal(run="run-1"), signal(run="run-2")
    candidate = (await engine.process([first])).artifacts[0]
    with pytest.raises(ArtifactConflictError, match="under review"):
        await engine.review(
            candidate.scope, candidate.artifact_id, reviewer="reviewer", approved=True
        )
    review = await engine.request_review(candidate.scope, candidate.artifact_id)
    assert await engine.request_review(candidate.scope, candidate.artifact_id) == review
    with pytest.raises(ArtifactConflictError, match="validated before activation"):
        await engine.activate(review.scope, review.artifact_id)
    with pytest.raises(LearningValidationError, match="validation policy"):
        await engine.review(review.scope, review.artifact_id, reviewer="reviewer", approved=True)
    assert (await repository.get(review.scope, review.artifact_id)).version == review.version
    ready = (await engine.process([second])).artifacts[0]
    validated = await engine.review(
        ready.scope, ready.artifact_id, reviewer="reviewer", approved=True
    )
    assert validated.status is ArtifactStatus.VALIDATED
    assert validated.validation_history[-1].reviewed_version == ready.version
    active = await engine.activate(validated.scope, validated.artifact_id)
    assert active.status is ArtifactStatus.ACTIVE
    result = await engine.retrieve(RetrievalRequest(scope=active.scope, agent_id="agent"))
    assert result.artifacts == (active,)
    assert not (
        await engine.retrieve(RetrievalRequest(scope=active.scope, agent_id="other"))
    ).artifacts


async def test_rejected_review_is_audited_and_terminal() -> None:
    repository = InMemoryArtifactRepository()
    engine = LearningEngine(repository)
    item = (await engine.process([signal()])).artifacts[0]
    await engine.request_review(item.scope, item.artifact_id)
    rejected = await engine.review(
        item.scope, item.artifact_id, reviewer="reviewer", approved=False, reason="unsupported"
    )
    assert rejected.status is ArtifactStatus.REJECTED
    assert rejected.validation_history[-1].reason == "unsupported"
    with pytest.raises(ArtifactConflictError, match="terminal"):
        await engine.process([signal(run="different")])


async def test_invalidated_feedback_requires_review_and_cannot_be_restored_by_replay() -> None:
    repository = InMemoryArtifactRepository()
    engine = LearningEngine(repository)
    first, second = signal(run="run-1"), signal(run="run-2")
    candidate = (await engine.process([first, second])).artifacts[0]
    await engine.request_review(candidate.scope, candidate.artifact_id)
    await engine.review(candidate.scope, candidate.artifact_id, reviewer="human", approved=True)
    active = await engine.activate(candidate.scope, candidate.artifact_id)
    assert first.evidence.feedback_id is not None
    invalidated = await engine.invalidate_feedback(active.scope, first.evidence.feedback_id)
    assert len(invalidated) == 1
    assert invalidated[0].status is ArtifactStatus.UNDER_REVIEW
    assert invalidated[0].evidence_count == 1
    assert first.evidence.feedback_id in invalidated[0].source_feedback_ids
    replayed = (await engine.process([first])).artifacts[0]
    assert replayed.version == invalidated[0].version
    assert replayed.evidence_count == 1
    assert (await engine.invalidate_feedback(active.scope, first.evidence.feedback_id))[
        0
    ] == replayed
    assert not (
        await engine.retrieve(RetrievalRequest(scope=active.scope, agent_id="agent"))
    ).artifacts


async def test_new_evidence_returns_active_artifact_to_review() -> None:
    repository = InMemoryArtifactRepository()
    engine = LearningEngine(repository)
    candidate = (await engine.process([signal(run="run-1"), signal(run="run-2")])).artifacts[0]

    def evolve(item, **changes):
        return item.model_copy(
            update={
                "version": item.version + 1,
                "updated_at": item.updated_at + timedelta(microseconds=1),
                **changes,
            }
        )

    review = await repository.update(
        evolve(candidate, status=ArtifactStatus.UNDER_REVIEW), expected_version=1
    )
    decision = ValidationRecord(
        reviewer="reviewer",
        approved=True,
        policy_version="default-v1",
        reviewed_version=review.version,
        artifact_digest=review.review_digest(),
    )
    validated = await repository.update(
        evolve(review, status=ArtifactStatus.VALIDATED, validation_history=(decision,)),
        expected_version=2,
    )
    active = await repository.update(
        evolve(validated, status=ArtifactStatus.ACTIVE), expected_version=3
    )
    assert (await engine.retrieve(RetrievalRequest(scope=active.scope, agent_id="agent"))).artifacts
    changed = (await engine.process([signal(run="run-3")])).artifacts[0]
    assert changed.status is ArtifactStatus.UNDER_REVIEW
    assert changed.version == 5
    assert changed.validation_history == active.validation_history
    assert changed.evidence_count == 3
    assert not (
        await engine.retrieve(RetrievalRequest(scope=active.scope, agent_id="agent"))
    ).artifacts


async def test_conflicting_signal_evidence_cannot_overwrite_existing_evidence() -> None:
    repository = InMemoryArtifactRepository()
    engine = LearningEngine(repository)
    original = signal()
    await engine.process([original])
    altered = original.model_copy(
        update={"evidence": original.evidence.model_copy(update={"source": "other"})}
    )
    with pytest.raises(ArtifactConflictError, match="conflicting content"):
        await engine.process([altered])


async def test_concurrent_batches_merge_without_losing_evidence() -> None:
    repository = InMemoryArtifactRepository()
    engine = LearningEngine(repository)
    signals = [signal(run=f"run-{number}") for number in range(10)]
    await asyncio.gather(*(engine.process([item]) for item in signals))
    result = await engine.process(signals)
    assert result.artifacts[0].evidence_count == 10
    assert result.artifacts[0].version == 10


async def test_invalidating_many_artifacts_paginates_and_preserves_audit() -> None:
    repository = InMemoryArtifactRepository()
    engine = LearningEngine(repository)
    original = signal()
    for number in range(51):
        await engine.process([original.model_copy(update={"content": f"lesson {number}"})])
    assert original.evidence.feedback_id is not None
    changed = await engine.invalidate_feedback(original.scope, original.evidence.feedback_id)
    assert len(changed) == 51
    assert all(item.evidence_count == 0 for item in changed)


async def test_terminal_artifact_refuses_evidence_invalidation() -> None:
    repository = InMemoryArtifactRepository()
    engine = LearningEngine(repository)
    original = signal()
    item = (await engine.process([original])).artifacts[0]
    await engine.request_review(item.scope, item.artifact_id)
    await engine.review(item.scope, item.artifact_id, reviewer="human", approved=False)
    assert original.evidence.feedback_id is not None
    with pytest.raises(ArtifactConflictError, match="terminal artifact"):
        await engine.invalidate_feedback(item.scope, original.evidence.feedback_id)


async def test_lifecycle_rejects_mis_scoped_repository_get() -> None:
    repository = InMemoryArtifactRepository()
    engine = LearningEngine(repository)
    item = (await engine.process([signal()])).artifacts[0]
    original_get = repository.get

    async def wrong_get(scope, artifact_id, *, version=None):
        result = await original_get(item.scope, artifact_id, version=version)
        return result.model_copy(
            update={"scope": ArtifactScope(tenant_id="other", agent_id="agent")}
        )

    repository.get = wrong_get
    with pytest.raises(LearningScopeError, match="out-of-scope"):
        await engine.request_review(TenantScope(tenant_id="tenant"), item.artifact_id)


async def test_activation_rechecks_changed_policy() -> None:
    repository = InMemoryArtifactRepository()
    engine = LearningEngine(repository)
    item = (await engine.process([signal(run="1"), signal(run="2")])).artifacts[0]
    await engine.request_review(item.scope, item.artifact_id)
    await engine.review(item.scope, item.artifact_id, reviewer="human", approved=True)
    stricter = LearningEngine(
        repository,
        validation_policy=DefaultLearningValidationPolicy(
            LearningValidationConfig(version="new-policy")
        ),
    )
    with pytest.raises(LearningValidationError, match="validation policy"):
        await stricter.activate(item.scope, item.artifact_id)


async def test_engine_fails_explicitly_after_conflicts_and_preserves_errors() -> None:
    repository = InMemoryArtifactRepository()
    engine = LearningEngine(repository)
    first, second = signal(run="1"), signal(run="2")
    with (
        patch.object(repository, "create", side_effect=ArtifactConflictError("race")),
        pytest.raises(ArtifactConflictError, match="changed repeatedly"),
    ):
        await engine.process([first])
    item = (await engine.process([first])).artifacts[0]
    for error in (
        ArtifactConflictError("race", current_version=2),
        ArtifactConflictError("broken"),
    ):
        message = "changed repeatedly" if error.context else "broken"
        with patch.object(repository, "update", side_effect=error):
            with pytest.raises(ArtifactConflictError, match=message):
                await engine.process([second])
            assert first.evidence.feedback_id is not None
            with pytest.raises(ArtifactConflictError, match=message):
                await engine.invalidate_feedback(item.scope, first.evidence.feedback_id)


async def test_engine_checks_repository_and_candidate_provenance() -> None:
    repository = InMemoryArtifactRepository()
    engine = LearningEngine(repository)
    first, second = signal(run="1"), signal(run="2")
    current = (await engine.process([first])).artifacts[0]
    learner = engine._learners[SignalType.IMPROVEMENT]
    candidate = learner.learn(engine.miner.mine([second])[0])
    foreign = current.model_copy(
        update={"scope": ArtifactScope(tenant_id="other", agent_id="agent")}
    )
    with (
        patch.object(repository, "get", return_value=foreign),
        pytest.raises(ArtifactConflictError, match="does not match"),
    ):
        await engine._merge(candidate)
    for bad in ({"signal_ids": "bad"}, {"signal_ids": [1]}):
        with pytest.raises(ArtifactConflictError, match="candidate signal provenance"):
            await engine._merge(candidate.model_copy(update={"provenance": bad}))
        with (
            patch.object(
                repository, "get", return_value=current.model_copy(update={"provenance": bad})
            ),
            pytest.raises(ArtifactConflictError, match="artifact signal provenance"),
        ):
            await engine._merge(candidate)


async def test_invalidation_rejects_invalid_repository_pages() -> None:
    repository = InMemoryArtifactRepository()
    engine = LearningEngine(repository)
    original = signal()
    item = (await engine.process([original])).artifacts[0]
    assert original.evidence.feedback_id is not None
    foreign = item.model_copy(update={"scope": ArtifactScope(tenant_id="other", agent_id="agent")})
    pages = (
        (ArtifactPage(artifacts=(item,) * 51), "page size"),
        (ArtifactPage(artifacts=(foreign,)), "unrequested"),
        (ArtifactPage(artifacts=(), next_cursor="bad"), "pagination cursor"),
    )
    for page, message in pages:
        with (
            patch.object(repository, "query", return_value=page),
            pytest.raises(LearningScopeError, match=message),
        ):
            await engine.invalidate_feedback(item.scope, original.evidence.feedback_id)
