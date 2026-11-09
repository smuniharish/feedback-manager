from datetime import timedelta
from uuid import uuid4

import pytest

from feedback_manager.learning import (
    ArtifactScope,
    ArtifactStatus,
    ArtifactType,
    Evidence,
    LearnedArtifact,
    LearningScopeError,
    ScopeVisibility,
    TenantScope,
    UnsupportedCapabilityError,
    ValidationRecord,
)
from feedback_manager.learning.validation import (
    DefaultLearningValidationPolicy,
    LearningValidationConfig,
)
from feedback_manager.retrieval import RetrievalRequest, StructuredRetriever
from feedback_manager.storage.artifacts import (
    ArtifactPage,
    ArtifactQuery,
    ArtifactRepository,
    InMemoryArtifactRepository,
)


def next_version(artifact: LearnedArtifact, **changes: object) -> LearnedArtifact:
    return artifact.model_copy(
        update={
            "version": artifact.version + 1,
            "updated_at": artifact.updated_at + timedelta(microseconds=1),
            **changes,
        }
    )


async def active_artifact(
    repository: ArtifactRepository,
    scope: ArtifactScope,
    *,
    content: str = "Search then validate results",
) -> LearnedArtifact:
    evidence = tuple(
        Evidence(feedback_id=uuid4(), source="human", independence_key=f"run-{number}")
        for number in range(2)
    )
    candidate = await repository.create(
        LearnedArtifact(
            artifact_type=ArtifactType.STRATEGY,
            title="Validate results",
            content=content,
            scope=scope,
            evidence=evidence,
            confidence=0.9,
        )
    )
    review = await repository.update(
        next_version(candidate, status=ArtifactStatus.UNDER_REVIEW), expected_version=1
    )
    record = ValidationRecord(
        reviewer="reviewer",
        approved=True,
        policy_version="default-v1",
        reviewed_version=review.version,
        artifact_digest=review.review_digest(),
    )
    validated = await repository.update(
        next_version(review, status=ArtifactStatus.VALIDATED, validation_history=(record,)),
        expected_version=2,
    )
    return await repository.update(
        next_version(validated, status=ArtifactStatus.ACTIVE), expected_version=3
    )


async def test_retrieval_isolates_tenants_namespaces_agents_and_task_scopes() -> None:
    repository = InMemoryArtifactRepository()
    private = await active_artifact(
        repository, ArtifactScope(tenant_id="tenant", agent_id="alice", task_type="research")
    )
    shared = await active_artifact(
        repository, ArtifactScope(tenant_id="tenant", visibility=ScopeVisibility.TENANT)
    )
    await active_artifact(repository, ArtifactScope(tenant_id="tenant", agent_id="bob"))
    await active_artifact(repository, ArtifactScope(tenant_id="other", agent_id="alice"))
    await active_artifact(
        repository, ArtifactScope(tenant_id="tenant", namespace="other", agent_id="alice")
    )
    retriever = StructuredRetriever(repository)
    request = RetrievalRequest(
        scope=TenantScope(tenant_id="tenant"),
        agent_id="alice",
        task_type="research",
        task="validate results",
    )
    result = await retriever.retrieve(request)
    assert {item.artifact_id for item in result.artifacts} == {
        private.artifact_id,
        shared.artifact_id,
    }
    assert all(item.confidence_score == pytest.approx(0.9) for item in result.results)
    assert all(item.evidence_count == 2 for item in result.results)
    assert all(item.explanation for item in result.results)
    without_task = await retriever.retrieve(request.model_copy(update={"task_type": None}))
    assert [item.artifact_id for item in without_task.artifacts] == [shared.artifact_id]
    without_agent = await retriever.retrieve(request.model_copy(update={"agent_id": None}))
    assert [item.artifact_id for item in without_agent.artifacts] == [shared.artifact_id]
    foreign = await retriever.retrieve(
        request.model_copy(update={"scope": TenantScope(tenant_id="unknown")})
    )
    assert not foreign.results


async def test_agent_type_visibility_and_applicability() -> None:
    repository = InMemoryArtifactRepository()
    scope = ArtifactScope(
        tenant_id="tenant", visibility=ScopeVisibility.AGENT_TYPE, agent_type="researcher"
    )
    artifact = await active_artifact(repository, scope)
    retriever = StructuredRetriever(repository)
    request = RetrievalRequest(scope=TenantScope(tenant_id="tenant"), agent_type="researcher")
    assert (await retriever.retrieve(request)).artifacts == (artifact,)
    assert not (
        await retriever.retrieve(request.model_copy(update={"agent_type": "developer"}))
    ).results


async def test_retrieval_bounds_results_candidates_and_rendered_context() -> None:
    repository = InMemoryArtifactRepository()
    scope = ArtifactScope(tenant_id="tenant", agent_id="agent")
    for number in range(3):
        await active_artifact(repository, scope, content=f"Validate result number {number}")
    retriever = StructuredRetriever(repository)
    request = RetrievalRequest(
        scope=TenantScope(tenant_id="tenant"), agent_id="agent", max_candidates=1
    )
    bounded = await retriever.retrieve(request)
    assert len(bounded.results) == 1
    assert bounded.truncated
    small = await retriever.retrieve(request.model_copy(update={"max_context_chars": 1}))
    assert not small.results
    assert small.truncated
    result = await retriever.retrieve(
        request.model_copy(update={"max_candidates": 10, "limit": 1, "max_context_chars": 100})
    )
    assert result.truncated
    assert len(result.as_context()) <= 100
    with pytest.raises(UnsupportedCapabilityError, match="cannot traverse"):
        await retriever.retrieve(request.model_copy(update={"graph_depth": 1}))


class LeakyRepository(InMemoryArtifactRepository):
    def __init__(self, artifact: LearnedArtifact) -> None:
        super().__init__()
        self.artifact = artifact

    async def query(self, query: ArtifactQuery) -> ArtifactPage:
        return ArtifactPage(artifacts=(self.artifact,))


class OversizedRepository(LeakyRepository):
    async def query(self, query: ArtifactQuery) -> ArtifactPage:
        return ArtifactPage(artifacts=(self.artifact, self.artifact))


class InvalidCursorRepository(InMemoryArtifactRepository):
    async def query(self, query: ArtifactQuery) -> ArtifactPage:
        return ArtifactPage(artifacts=(), next_cursor="invalid")


async def test_retrieval_rechecks_status_review_policy_and_confidence() -> None:
    repository = InMemoryArtifactRepository()
    scope = ArtifactScope(tenant_id="tenant", agent_id="agent")
    artifact = await active_artifact(repository, scope)
    request = RetrievalRequest(scope=TenantScope(tenant_id="tenant"), agent_id="agent")
    retriever = StructuredRetriever(
        LeakyRepository(artifact.model_copy(update={"status": ArtifactStatus.CANDIDATE}))
    )
    with pytest.raises(LearningScopeError, match="unrequested"):
        await retriever.retrieve(request)
    retriever = StructuredRetriever(repository)
    assert not (
        await retriever.retrieve(request.model_copy(update={"min_confidence": 1.0}))
    ).results
    retriever = StructuredRetriever(
        repository,
        validation_policy=DefaultLearningValidationPolicy(LearningValidationConfig(version="v2")),
    )
    assert not (await retriever.retrieve(request)).results
    with pytest.raises(LearningScopeError, match="pagination cursor"):
        await StructuredRetriever(InvalidCursorRepository()).retrieve(request)


async def test_retrieval_enforces_repository_page_bounds() -> None:
    repository = InMemoryArtifactRepository()
    artifact = await active_artifact(
        repository, ArtifactScope(tenant_id="tenant", agent_id="agent")
    )
    retriever = StructuredRetriever(OversizedRepository(artifact))
    with pytest.raises(LearningScopeError, match="page size"):
        await retriever.retrieve(
            RetrievalRequest(
                scope=TenantScope(tenant_id="tenant"), agent_id="agent", max_candidates=1
            )
        )
    with pytest.raises(LearningScopeError, match="duplicate artifact"):
        await retriever.retrieve(
            RetrievalRequest(scope=TenantScope(tenant_id="tenant"), agent_id="agent")
        )


async def test_retrieval_fails_closed_for_a_misbehaving_repository() -> None:
    repository = InMemoryArtifactRepository()
    foreign = await active_artifact(repository, ArtifactScope(tenant_id="other", agent_id="agent"))
    retriever = StructuredRetriever(LeakyRepository(foreign))
    with pytest.raises(LearningScopeError, match="out-of-scope"):
        await retriever.retrieve(
            RetrievalRequest(scope=TenantScope(tenant_id="tenant"), agent_id="agent")
        )
