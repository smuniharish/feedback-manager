from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from feedback_manager import FeedbackManager, FeedbackStatus
from feedback_manager.assessment import LearningDecision
from feedback_manager.embeddings import HashingEmbeddingProvider
from feedback_manager.learning.assessment_verification import (
    AssessmentVerification,
    AssessmentVerifier,
)
from feedback_manager.learning.engine import LearningEngine
from feedback_manager.learning.errors import (
    LearningScopeError,
    LearningValidationError,
    UnsupportedCapabilityError,
)
from feedback_manager.learning.jobs import (
    InMemoryLearningJobStore,
    LearningJob,
    LearningJobStatus,
    LearningLeaseLostError,
)
from feedback_manager.learning.models import ArtifactScope, Evidence, ExecutionOutcome
from feedback_manager.learning.signals import LearningSignalExtractor
from feedback_manager.learning.worker import BackgroundLearner, ContextScopeResolver
from feedback_manager.retrieval import (
    ArtifactIndexer,
    RetrievalRequest,
    StructuredRetriever,
    VectorRetriever,
)
from feedback_manager.retrieval.base import LearnedArtifactRetriever
from feedback_manager.retrieval.models import RetrievalResult, RetrievedArtifact
from feedback_manager.storage.artifacts import ArtifactQuery, InMemoryArtifactRepository
from feedback_manager.storage.vector import InMemoryVectorStore
from tests.learning.test_assessment import SCOPE
from tests.learning.test_assessment_verification import verified_setup
from tests.learning.test_retrieval import active_artifact


async def test_engine_rejects_unassessed_and_tampered_feedback_signals() -> None:
    verifier, feedback, _, _, assessment, _ = await verified_setup(text_correction=True)
    scope = ArtifactScope.model_validate(SCOPE.model_dump())
    extraction = LearningSignalExtractor().extract(feedback, scope=scope)
    assert extraction.signal is not None
    unassessed = extraction.signal
    assessed = unassessed.model_copy(
        update={
            "evidence": unassessed.evidence.model_copy(
                update={
                    "assessment_id": assessment.assessment_id,
                    "assessment_version": assessment.version,
                }
            )
        }
    )
    repository = InMemoryArtifactRepository()
    engine = LearningEngine(repository, assessment_verifier=verifier)
    with pytest.raises(LearningValidationError, match="assessment"):
        await engine.process([unassessed])
    with pytest.raises(LearningValidationError, match="signal_mismatch"):
        await engine.process([assessed.model_copy(update={"content": "fabricated"})])
    assert not (await repository.query(ArtifactQuery(scope=scope))).artifacts
    assert (await engine.process([assessed])).artifacts
    await verifier.feedback.transition(
        feedback.feedback_id, FeedbackStatus.ACKNOWLEDGED, expected=FeedbackStatus.RECEIVED
    )
    with pytest.raises(LearningValidationError, match="feedback_changed"):
        await engine.process([assessed])


async def test_engine_without_verifier_does_not_accept_feedback_but_allows_outcomes() -> None:
    _, feedback, _, _, _, _ = await verified_setup(text_correction=True)
    scope = ArtifactScope.model_validate(SCOPE.model_dump())
    signal = LearningSignalExtractor().extract(feedback, scope=scope).signal
    assert signal is not None
    engine = LearningEngine(InMemoryArtifactRepository())
    with pytest.raises(LearningValidationError, match="assessment"):
        await engine.process([signal])
    outcome = ExecutionOutcome(execution_id="run", scope=scope, success=True, description="done")
    assert (await engine.process([LearningSignalExtractor().extract_outcome(outcome)])).artifacts


async def test_custom_signal_extractor_is_reused_for_authoritative_verification() -> None:
    verifier, feedback, _, _, assessment, _ = await verified_setup(text_correction=True)

    class CustomExtractor(LearningSignalExtractor):
        def extract(self, feedback, *, scope):
            result = super().extract(feedback, scope=scope)
            if result.signal is None:
                return result
            return result.model_copy(
                update={
                    "signal": result.signal.model_copy(
                        update={"content": f"Custom: {result.signal.content}"}
                    )
                }
            )

    custom = CustomExtractor()
    verifier = AssessmentVerifier(
        verifier.feedback, verifier.experiences, verifier.assessments, extractor=custom
    )
    manager = FeedbackManager(store=verifier.feedback, learning=False)
    repository = InMemoryArtifactRepository()
    worker = BackgroundLearner(
        manager,
        LearningEngine(repository, assessment_verifier=verifier),
        InMemoryLearningJobStore(),
        scope_resolver=ContextScopeResolver(namespace="app"),
    )
    await worker.enqueue_assessment(
        feedback.feedback_id, assessment.assessment_id, assessment_version=assessment.version
    )
    await worker.drain()
    (artifact,) = (await repository.query(ArtifactQuery(scope=SCOPE))).artifacts
    assert artifact.content == "Custom: corrected result"
    assert worker.extractor is custom


async def test_worker_enqueues_an_accepted_assessment_after_the_feedback_job() -> None:
    verifier, feedback, _, _, assessment, _ = await verified_setup(text_correction=True)
    manager = FeedbackManager(store=verifier.feedback, learning=False)
    repository = InMemoryArtifactRepository()
    engine = LearningEngine(repository, assessment_verifier=verifier)
    worker = BackgroundLearner(
        manager,
        engine,
        InMemoryLearningJobStore(),
        scope_resolver=ContextScopeResolver(namespace="app"),
    )
    unassessed = await worker.enqueue(feedback)
    assert unassessed is not None
    assert await worker.drain() == 1
    assert (await worker.store.get(unassessed.job_id)).status is LearningJobStatus.SUCCEEDED
    assert not (await repository.query(ArtifactQuery(scope=SCOPE))).artifacts
    assessed = await worker.enqueue_assessment(
        feedback.feedback_id, assessment.assessment_id, assessment_version=assessment.version
    )
    assert assessed.job_id != unassessed.job_id
    assert (
        await worker.enqueue_assessment(
            feedback.feedback_id, assessment.assessment_id, assessment_version=assessment.version
        )
    ).job_id == assessed.job_id
    assert await worker.drain() == 1
    assert worker.stats.learned == 1
    artifact = (await repository.query(ArtifactQuery(scope=SCOPE))).artifacts[0]
    assert artifact.evidence[0].assessment_id == assessment.assessment_id
    assert artifact.evidence[0].assessment_version == assessment.version
    await manager.aclose()


async def test_retrieval_quarantines_legacy_and_stale_feedback_evidence() -> None:
    verifier, feedback, _, _, assessment, _ = await verified_setup(text_correction=True)
    scope = ArtifactScope.model_validate(SCOPE.model_dump())
    signal = LearningSignalExtractor().extract(feedback, scope=scope).signal
    assert signal is not None
    assessed = signal.evidence.model_copy(
        update={
            "assessment_id": assessment.assessment_id,
            "assessment_version": assessment.version,
        }
    )
    independent = Evidence(execution_id="independent", source="agent", independence_key="other")
    repository = InMemoryArtifactRepository()
    active = await active_artifact(repository, scope, evidence=(assessed, independent))
    legacy = await active_artifact(
        repository, scope, content="Legacy lesson", evidence=(signal.evidence, independent)
    )
    request = RetrievalRequest(scope=scope, agent_id="agent")
    assert not (await StructuredRetriever(repository).retrieve(request)).artifacts
    retriever = StructuredRetriever(repository, assessment_verifier=verifier)
    assert (await retriever.retrieve(request)).artifacts == (active,)
    assert legacy not in (await retriever.retrieve(request)).artifacts
    vectors = InMemoryVectorStore()
    provider = HashingEmbeddingProvider()
    await ArtifactIndexer(repository, vectors, provider).index(scope, active.artifact_id)
    vector_request = request.model_copy(update={"task": "validate"})
    assert not (
        await VectorRetriever(repository, vectors, provider).retrieve(vector_request)
    ).artifacts
    vector = VectorRetriever(repository, vectors, provider, assessment_verifier=verifier)
    assert (await vector.retrieve(vector_request)).artifacts == (active,)
    await verifier.feedback.transition(
        feedback.feedback_id, FeedbackStatus.ACKNOWLEDGED, expected=FeedbackStatus.RECEIVED
    )
    assert not (await retriever.retrieve(request)).artifacts
    assert not (await vector.retrieve(vector_request)).artifacts


async def test_reassessment_updates_evidence_without_restoring_invalidation() -> None:
    verifier, feedback, _, _, assessment, _ = await verified_setup(text_correction=True)
    scope = ArtifactScope.model_validate(SCOPE.model_dump())
    signal = LearningSignalExtractor().extract(feedback, scope=scope).signal
    assert signal is not None
    evidence = signal.evidence.model_copy(
        update={
            "assessment_id": assessment.assessment_id,
            "assessment_version": assessment.version,
        }
    )
    signal = signal.model_copy(update={"evidence": evidence})
    engine = LearningEngine(InMemoryArtifactRepository(), assessment_verifier=verifier)
    artifact = (await engine.process([signal])).artifacts[0]
    invalidated = (await engine.invalidate_feedback(scope, feedback.feedback_id))[0]
    assert not invalidated.evidence[0].valid
    newer = assessment.model_copy(
        update={
            "version": assessment.version + 1,
            "assessed_at": assessment.assessed_at + timedelta(microseconds=1),
        }
    )
    await verifier.assessments.update(newer, expected_version=assessment.version)
    await verifier.assessments.decide(
        LearningDecision(
            scope=SCOPE,
            assessment_id=newer.assessment_id,
            assessment_version=newer.version,
            accepted=True,
            reviewer="reviewer",
        )
    )
    updated_signal = signal.model_copy(
        update={"evidence": evidence.model_copy(update={"assessment_version": newer.version})}
    )
    with pytest.raises(LearningValidationError, match="assessment_version_changed"):
        await engine.process([signal])
    revised = (await engine.process([updated_signal])).artifacts[0]
    assert revised.artifact_id == artifact.artifact_id
    assert revised.version == invalidated.version + 1
    assert revised.evidence[0].assessment_version == newer.version
    assert not revised.evidence[0].valid


async def test_verifier_fails_closed_when_sources_change_between_reads() -> None:
    verifier, feedback, _, _, assessment, _ = await verified_setup(text_correction=True)
    scope = ArtifactScope.model_validate(SCOPE.model_dump())
    signal = LearningSignalExtractor().extract(feedback, scope=scope).signal
    assert signal is not None
    evidence = signal.evidence.model_copy(
        update={"assessment_id": assessment.assessment_id, "assessment_version": assessment.version}
    )
    signal = signal.model_copy(update={"evidence": evidence})
    artifact = await active_artifact(
        InMemoryArtifactRepository(),
        scope,
        evidence=(
            evidence,
            Evidence(execution_id="independent", source="agent", independence_key="other"),
        ),
    )
    accepted = AssessmentVerification(eligible=True, reason="accepted")
    denied = AssessmentVerification(eligible=False, reason="changed")
    with patch.object(verifier, "verify", return_value=accepted):
        with patch.object(verifier.feedback, "get", return_value=None):
            assert (await verifier.verify_signal(signal)).reason == "feedback_not_found"
            assert (await verifier.verify_artifact(artifact)).reason == "feedback_not_found"
        invalid = feedback.model_copy(update={"payload": {}})
        with patch.object(verifier.feedback, "get", return_value=invalid):
            assert (await verifier.verify_signal(signal)).reason == "signal_mismatch"
            assert (await verifier.verify_artifact(artifact)).reason == "evidence_mismatch"
        forged = artifact.model_copy(
            update={"evidence": (evidence.model_copy(update={"source": "other"}),)}
        )
        assert (await verifier.verify_artifact(forged)).reason == "evidence_mismatch"
    with patch.object(verifier, "verify", side_effect=[accepted, denied]):
        assert (await verifier.verify_artifact(artifact)).reason == "changed"


async def test_assessment_enqueue_rejects_missing_or_unauthorized_inputs() -> None:
    verifier, feedback, _, _, assessment, _ = await verified_setup(text_correction=True)
    manager = FeedbackManager(store=verifier.feedback, learning=False)
    repository = InMemoryArtifactRepository()
    worker = BackgroundLearner(
        manager,
        LearningEngine(repository),
        InMemoryLearningJobStore(),
        scope_resolver=ContextScopeResolver(namespace="app"),
    )
    with pytest.raises(UnsupportedCapabilityError, match="verifier"):
        await worker.enqueue_assessment(
            feedback.feedback_id, assessment.assessment_id, assessment_version=1
        )
    worker.engine = LearningEngine(repository, assessment_verifier=verifier)
    with pytest.raises(LearningValidationError, match="no longer exists"):
        await worker.enqueue_assessment(uuid4(), assessment.assessment_id, assessment_version=1)
    worker.scope_resolver = ContextScopeResolver()
    with pytest.raises(LearningScopeError, match="trusted"):
        await worker.enqueue_assessment(
            feedback.feedback_id, assessment.assessment_id, assessment_version=1
        )
    worker.scope_resolver = ContextScopeResolver(namespace="app")
    with pytest.raises(LearningValidationError, match="assessment_version_changed"):
        await worker.enqueue_assessment(
            feedback.feedback_id, assessment.assessment_id, assessment_version=2
        )
    job = await worker.enqueue_assessment(
        feedback.feedback_id, assessment.assessment_id, assessment_version=assessment.version
    )
    await verifier.feedback.transition(
        feedback.feedback_id, FeedbackStatus.ACKNOWLEDGED, expected=FeedbackStatus.RECEIVED
    )
    await worker.drain()
    assert (await worker.store.get(job.job_id)).status is LearningJobStatus.DEAD_LETTERED
    assert not (await repository.query(ArtifactQuery(scope=SCOPE))).artifacts


def test_assessment_references_must_be_complete() -> None:
    with pytest.raises(ValidationError, match="supplied together"):
        Evidence(feedback_id=uuid4(), assessment_id=uuid4(), source="human", independence_key="run")
    with pytest.raises(ValidationError, match="requires a feedback_id"):
        Evidence(
            execution_id="run",
            assessment_id=uuid4(),
            assessment_version=1,
            source="human",
            independence_key="run",
        )
    with pytest.raises(ValidationError, match="supplied together"):
        LearningJob(
            job_id=uuid4(),
            feedback_id=uuid4(),
            feedback_status="received",
            scope=ArtifactScope.model_validate(SCOPE.model_dump()),
            assessment_id=uuid4(),
        )


async def test_stale_evidence_prevents_review_and_activation() -> None:
    verifier, feedback, _, _, assessment, _ = await verified_setup(text_correction=True)
    scope = ArtifactScope.model_validate(SCOPE.model_dump())
    signal = LearningSignalExtractor().extract(feedback, scope=scope).signal
    assert signal is not None
    evidence = signal.evidence.model_copy(
        update={"assessment_id": assessment.assessment_id, "assessment_version": assessment.version}
    )
    repository = InMemoryArtifactRepository()
    active = await active_artifact(
        repository,
        scope,
        evidence=(
            evidence,
            Evidence(execution_id="other", source="agent", independence_key="other"),
        ),
    )
    engine = LearningEngine(repository, assessment_verifier=verifier)
    await engine.request_review(scope, active.artifact_id)
    validated = await engine.review(scope, active.artifact_id, reviewer="reviewer", approved=True)
    await verifier.feedback.transition(
        feedback.feedback_id, FeedbackStatus.ACKNOWLEDGED, expected=FeedbackStatus.RECEIVED
    )
    with pytest.raises(LearningValidationError, match="assessment verification"):
        await engine.activate(scope, validated.artifact_id)
    await engine.request_review(scope, active.artifact_id)
    with pytest.raises(LearningValidationError, match="assessment verification"):
        await engine.review(scope, active.artifact_id, reviewer="reviewer", approved=True)


async def test_invalidated_feedback_remains_auditable_without_blocking_other_support() -> None:
    verifier, feedback, _, _, _, _ = await verified_setup(text_correction=True)
    scope = ArtifactScope.model_validate(SCOPE.model_dump())
    repository = InMemoryArtifactRepository()
    evidence = (
        Evidence(
            feedback_id=feedback.feedback_id, source="human", independence_key="old", valid=False
        ),
        Evidence(execution_id="one", source="agent", independence_key="one"),
        Evidence(execution_id="two", source="agent", independence_key="two"),
    )
    artifact = await active_artifact(repository, scope, evidence=evidence)
    engine = LearningEngine(repository, assessment_verifier=verifier)
    legacy_engine = None
    try:
        await engine.request_review(scope, artifact.artifact_id)
        await engine.review(scope, artifact.artifact_id, reviewer="reviewer", approved=True)
        active = await engine.activate(scope, artifact.artifact_id)
        assert not active.evidence[0].valid
        request = RetrievalRequest(scope=scope, agent_id="agent", task="validate results")
        assert (await engine.retrieve(request)).artifacts == (active,)
        await engine.aclose()
        legacy_engine = LearningEngine(repository)
        assert (await legacy_engine.retrieve(request)).artifacts == (active,)
    finally:
        await engine.aclose()
        if legacy_engine is not None:
            await legacy_engine.aclose()


async def test_worker_handles_lost_leases_and_unlearnable_assessed_jobs() -> None:
    verifier, feedback, _, _, assessment, _ = await verified_setup(text_correction=True)
    manager = FeedbackManager(store=verifier.feedback, learning=False)
    worker = BackgroundLearner(
        manager,
        LearningEngine(InMemoryArtifactRepository(), assessment_verifier=verifier),
        InMemoryLearningJobStore(),
        scope_resolver=ContextScopeResolver(namespace="app"),
    )
    job = await worker.enqueue_assessment(
        feedback.feedback_id, assessment.assessment_id, assessment_version=assessment.version
    )
    with (
        patch.object(worker, "_learn", side_effect=OSError()),
        patch.object(worker, "_fail", side_effect=LearningLeaseLostError("lease lost")),
    ):
        await worker.drain()
    assert (await worker.store.get(job.job_id)).status is LearningJobStatus.RUNNING
    unlearnable = feedback.model_copy(update={"payload": {}})
    with patch.object(manager, "get", return_value=unlearnable):
        await worker._learn_feedback(job)
    assert worker.stats.ignored == 1


class SuppliedRetriever(LearnedArtifactRetriever):
    def __init__(self, item: RetrievedArtifact) -> None:
        self.item = item

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        return RetrievalResult(results=(self.item,))


async def test_engine_guards_supplied_retrievers_and_rechecks_current_snapshot() -> None:
    scope = ArtifactScope.model_validate(SCOPE.model_dump())
    repository = InMemoryArtifactRepository()
    artifact = await active_artifact(repository, scope)
    item = RetrievedArtifact(
        artifact=artifact, relevance_score=1, confidence_score=1, evidence_count=2, explanation=()
    )
    engine = LearningEngine(repository, retriever=SuppliedRetriever(item))
    request = RetrievalRequest(scope=scope, agent_id="agent")
    assert (await engine.retrieve(request)).artifacts == (artifact,)
    assert not (await engine.retrieve(request.model_copy(update={"min_confidence": 1}))).artifacts
    with pytest.raises(LearningScopeError, match="unrequested"):
        await engine.retrieve(request.model_copy(update={"agent_id": "other"}))
    with patch.object(repository, "get", return_value=artifact.model_copy(update={"version": 5})):
        assert not (await engine.retrieve(request)).artifacts
    vectors, provider = InMemoryVectorStore(), HashingEmbeddingProvider()
    await ArtifactIndexer(repository, vectors, provider).index(scope, artifact.artifact_id)
    vector = VectorRetriever(repository, vectors, provider)
    with patch.object(vector.structured, "current_and_assessed", side_effect=[True, False]):
        assert not (
            await vector.retrieve(request.model_copy(update={"task": "validate"}))
        ).artifacts
