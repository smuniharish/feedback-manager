import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from retrievalloom import (
    EngineConfig,
    EngineState,
    LifecycleConfig,
    ResultStatus,
    RetrievalConfig,
    RetrievalEngine,
    TenantPolicy,
)

from feedback_manager.learning.engine import LearningEngine
from feedback_manager.learning.errors import (
    ArtifactNotFoundError,
    LearningScopeError,
    UnsupportedCapabilityError,
)
from feedback_manager.learning.models import (
    ArtifactScope,
    ArtifactStatus,
    ArtifactType,
    Evidence,
    ExecutionOutcome,
    LearnedArtifact,
    ScopeVisibility,
    TenantScope,
    ValidationRecord,
)
from feedback_manager.learning.signals import LearningSignalExtractor
from feedback_manager.retrieval import RetrievalLoomRetriever, RetrievalRequest
from feedback_manager.retrieval.base import LearnedArtifactRetriever
from feedback_manager.retrieval.models import RetrievalResult
from feedback_manager.retrieval.retrievalloom import (
    _document_id,
    _filters,
    _graph_representation,
    _parse_document_id,
    _retrieval_scope,
)
from feedback_manager.storage.artifacts import (
    ArtifactPage,
    InMemoryArtifactRepository,
    ProjectionIntent,
)


async def active_artifact(
    repository: InMemoryArtifactRepository,
    scope: ArtifactScope,
    *,
    content: str = "Validate query results",
) -> LearnedArtifact:
    evidence = tuple(
        Evidence(source="human", independence_key=f"run-{index}", execution_id=f"run-{index}")
        for index in range(2)
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
    review = candidate.model_copy(
        update={
            "status": ArtifactStatus.UNDER_REVIEW,
            "version": candidate.version + 1,
            "updated_at": candidate.updated_at + timedelta(microseconds=1),
        }
    )
    review = await repository.update(review, expected_version=candidate.version)
    record = ValidationRecord(
        reviewer="reviewer",
        approved=True,
        policy_version="default-v1",
        reviewed_version=review.version,
        artifact_digest=review.review_digest(),
    )
    validated = review.model_copy(
        update={
            "status": ArtifactStatus.VALIDATED,
            "version": review.version + 1,
            "updated_at": review.updated_at + timedelta(microseconds=1),
            "validation_history": (record,),
        }
    )
    validated = await repository.update(validated, expected_version=review.version)
    active = validated.model_copy(
        update={
            "status": ArtifactStatus.ACTIVE,
            "version": validated.version + 1,
            "updated_at": validated.updated_at + timedelta(microseconds=1),
        }
    )
    return await repository.update(active, expected_version=validated.version)


class FakeEvidence:
    def __init__(self, scope: object, document_id: str) -> None:
        self.evidence_id = "evidence-1"
        self.score = 0.8
        self.sources = (SimpleNamespace(scope=scope, document_id=document_id),)
        self.provenance = SimpleNamespace(hits=())

    def model_dump(self, *, mode: str) -> dict[str, str]:
        return {"evidence_id": self.evidence_id, "mode": mode}


def fake_response(
    evidence: tuple[FakeEvidence, ...], *, status: ResultStatus = ResultStatus.COMPLETE
) -> SimpleNamespace:
    return SimpleNamespace(
        evidence=evidence,
        status=status,
        snapshot=SimpleNamespace(model_dump=lambda *, mode: {"mode": mode}),
        warnings=(),
    )


class CallerOwnedRetriever(LearnedArtifactRetriever):
    def __init__(self) -> None:
        self.closed = False

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        return RetrievalResult(results=())

    async def aclose(self) -> None:
        self.closed = True


async def test_learning_engine_does_not_close_an_injected_retriever() -> None:
    scope = ArtifactScope(tenant_id="tenant", agent_id="agent")
    retriever = CallerOwnedRetriever()
    learner = LearningEngine(InMemoryArtifactRepository(), retriever=retriever)
    extractor = LearningSignalExtractor()
    signals = [
        extractor.extract_outcome(
            ExecutionOutcome(
                execution_id=f"run-{index}",
                scope=scope,
                success=True,
                description="Verify source facts before summarizing.",
            )
        )
        for index in range(2)
    ]
    assert (await learner.process(signals)).artifacts
    await learner.aclose()
    assert not retriever.closed


def test_document_id_parser_rejects_malformed_identifiers() -> None:
    prefix = "feedback-manager:artifact:"
    for value in ("foreign", f"{prefix}bad", f"{prefix}not-a-uuid:v1"):
        assert _parse_document_id(value) is None

    artifact_id = uuid4()
    assert _parse_document_id(f"{prefix}{artifact_id}:vx") is None
    assert _parse_document_id(f"{prefix}{artifact_id}:v0") is None


def test_retrievalloom_filters_include_agent_type_and_artifact_types() -> None:
    request = RetrievalRequest(
        scope=TenantScope(tenant_id="tenant"),
        agent_id="alice",
        agent_type="researcher",
        artifact_types=(ArtifactType.STRATEGY,),
        task="validate",
    )
    assert _filters(request)


async def test_retrievalloom_direct_index_and_close_before_initialization() -> None:
    repository = InMemoryArtifactRepository()
    artifact = await active_artifact(
        repository, ArtifactScope(tenant_id="tenant", agent_id="agent")
    )
    retriever = RetrievalLoomRetriever(repository)
    await retriever.aclose()
    assert retriever.engine is None
    with pytest.raises(UnsupportedCapabilityError, match="not initialized"):
        retriever._require_engine()

    await retriever.index(artifact)
    assert retriever.engine is not None
    scope_key = _retrieval_scope(artifact.scope).key
    retriever._indexed_scopes.add(scope_key)
    with patch.object(repository, "atomic_projection_intents", True):
        await retriever.project(artifact)
    assert scope_key in retriever._indexed_scopes
    await retriever.aclose()
    assert retriever.engine.state is EngineState.CLOSED
    await retriever.aclose()


async def test_retrievalloom_reindex_keeps_inactive_artifacts_out_of_active_set() -> None:
    repository = InMemoryArtifactRepository()
    scope = ArtifactScope(tenant_id="tenant", agent_id="agent")
    active = await active_artifact(repository, scope)
    candidate = await repository.create(
        LearnedArtifact(
            artifact_type=ArtifactType.STRATEGY,
            title="Unreviewed candidate",
            content="This must not enter the active index.",
            scope=scope,
        )
    )
    retriever = RetrievalLoomRetriever(repository)
    try:
        with patch.object(
            repository,
            "query",
            AsyncMock(return_value=ArtifactPage(artifacts=(active, candidate))),
        ):
            assert await retriever.reindex(TenantScope(tenant_id="tenant")) == 1
        assert retriever._indexed_artifacts[_retrieval_scope(scope).key] == {active.artifact_id}
    finally:
        await retriever.aclose()


async def test_retrievalloom_drain_intents_projects_and_removes_deleted_artifacts() -> None:
    repository = InMemoryArtifactRepository()
    artifact = await active_artifact(
        repository, ArtifactScope(tenant_id="tenant", agent_id="agent")
    )
    scope = TenantScope(tenant_id="tenant")
    intent = ProjectionIntent.for_artifact(artifact)
    retriever = RetrievalLoomRetriever(repository)
    pending = AsyncMock(return_value=(intent,))
    acknowledge = AsyncMock()
    try:
        with (
            patch.object(repository, "atomic_projection_intents", True),
            patch.object(repository, "pending_projections", pending),
            patch.object(repository, "acknowledge_projection", acknowledge),
        ):
            assert await retriever.drain_intents(scope) == 1
        acknowledge.assert_awaited_once_with(scope, intent.intent_id)

        assert retriever.engine is not None
        delete = AsyncMock()
        with (
            patch.object(repository, "atomic_projection_intents", True),
            patch.object(repository, "pending_projections", AsyncMock(return_value=(intent,))),
            patch.object(repository, "acknowledge_projection", AsyncMock()),
            patch.object(
                repository, "get", AsyncMock(side_effect=ArtifactNotFoundError("missing"))
            ),
            patch.object(retriever.engine, "delete", delete),
        ):
            assert await retriever.drain_intents(scope) == 1
        delete.assert_awaited_once()
    finally:
        await retriever.aclose()


async def test_retrievalloom_drain_intents_rejects_untrusted_outbox_entries() -> None:
    repository = InMemoryArtifactRepository()
    artifact = await active_artifact(
        repository, ArtifactScope(tenant_id="tenant", agent_id="agent")
    )
    foreign = await active_artifact(
        repository, ArtifactScope(tenant_id="foreign", agent_id="agent")
    )
    scope = TenantScope(tenant_id="tenant")
    intent = ProjectionIntent.for_artifact(artifact)
    retriever = RetrievalLoomRetriever(repository)
    try:
        with pytest.raises(UnsupportedCapabilityError, match="outbox"):
            await retriever.drain_intents(scope)
        with (
            patch.object(repository, "atomic_projection_intents", True),
            pytest.raises(ValueError, match="limit"),
        ):
            await retriever.drain_intents(scope, limit=0)

        with (
            patch.object(repository, "atomic_projection_intents", True),
            patch.object(
                repository,
                "pending_projections",
                AsyncMock(return_value=(intent, intent)),
            ),
            pytest.raises(LearningScopeError, match="page size"),
        ):
            await retriever.drain_intents(scope, limit=1)

        foreign_intent = intent.model_copy(update={"scope": TenantScope(tenant_id="foreign")})
        with (
            patch.object(repository, "atomic_projection_intents", True),
            patch.object(
                repository, "pending_projections", AsyncMock(return_value=(foreign_intent,))
            ),
            pytest.raises(LearningScopeError, match="out-of-scope intent"),
        ):
            await retriever.drain_intents(scope)

        with (
            patch.object(repository, "atomic_projection_intents", True),
            patch.object(repository, "pending_projections", AsyncMock(return_value=(intent,))),
            patch.object(repository, "get", AsyncMock(return_value=foreign)),
            pytest.raises(LearningScopeError, match="out-of-scope result"),
        ):
            await retriever.drain_intents(scope)
    finally:
        await retriever.aclose()


async def test_retrievalloom_reindex_rejects_invalid_repository_pages() -> None:
    repository = InMemoryArtifactRepository()
    artifact = await active_artifact(
        repository, ArtifactScope(tenant_id="tenant", agent_id="agent")
    )
    foreign = await active_artifact(
        repository, ArtifactScope(tenant_id="foreign", agent_id="agent")
    )
    retriever = RetrievalLoomRetriever(repository)
    scope = TenantScope(tenant_id="tenant")
    try:
        cases = (
            (SimpleNamespace(artifacts=(artifact,) * 1001, next_cursor=None), "page size"),
            (ArtifactPage(artifacts=(artifact, artifact)), "duplicate"),
            (ArtifactPage(artifacts=(foreign,)), "out-of-scope"),
            (ArtifactPage(artifacts=(), next_cursor="cursor"), "invalid cursor"),
        )
        for page, message in cases:
            with (
                patch.object(repository, "query", AsyncMock(return_value=page)),
                pytest.raises(LearningScopeError, match=message),
            ):
                await retriever.reindex(scope)

        second = await active_artifact(
            repository, ArtifactScope(tenant_id="tenant", agent_id="agent")
        )
        with (
            patch.object(
                repository,
                "query",
                AsyncMock(
                    side_effect=(
                        ArtifactPage(artifacts=(artifact,), next_cursor="cursor"),
                        ArtifactPage(artifacts=(second,), next_cursor="cursor"),
                    )
                ),
            ),
            pytest.raises(LearningScopeError, match="invalid cursor"),
        ):
            await retriever.reindex(scope)
    finally:
        await retriever.aclose()


async def test_retrievalloom_index_reports_failed_projection() -> None:
    repository = InMemoryArtifactRepository()
    artifact = await active_artifact(
        repository, ArtifactScope(tenant_id="tenant", agent_id="agent")
    )
    retriever = RetrievalLoomRetriever(repository)
    try:
        await retriever._ensure_open()
        assert retriever.engine is not None
        with (
            patch.object(
                retriever.engine,
                "ingest",
                AsyncMock(return_value=SimpleNamespace(documents=(SimpleNamespace(error="bad"),))),
            ),
            pytest.raises(LearningScopeError, match="could not index"),
        ):
            await retriever.index(artifact)
    finally:
        await retriever.aclose()


async def test_retrievalloom_retrieval_rejects_invalid_engine_evidence() -> None:
    repository = InMemoryArtifactRepository()
    artifact = await active_artifact(
        repository, ArtifactScope(tenant_id="tenant", agent_id="agent")
    )
    request = RetrievalRequest(
        scope=TenantScope(tenant_id="tenant"),
        agent_id="agent",
        task="validate",
        max_candidates=1,
    )
    engine = RetrievalEngine(
        EngineConfig(
            lifecycle=LifecycleConfig(background=False),
            retrieval=RetrievalConfig(
                default_tenant_policy=TenantPolicy(max_top_k=1000, max_candidate_k=1000)
            ),
        )
    )
    retriever = RetrievalLoomRetriever(repository, engine=engine)
    await engine.initialize()
    retriever._indexed_scopes.add(_retrieval_scope(request.scope).key)
    valid_id = _document_id(artifact.artifact_id, artifact.version)
    valid_scope = _retrieval_scope(request.scope)
    try:
        with (
            patch.object(
                engine,
                "retrieve",
                AsyncMock(return_value=fake_response((FakeEvidence(valid_scope, valid_id),) * 2)),
            ),
            pytest.raises(LearningScopeError, match="candidate bound"),
        ):
            await retriever.retrieve(request)

        with (
            patch.object(
                engine,
                "retrieve",
                AsyncMock(
                    return_value=fake_response(
                        (FakeEvidence(_retrieval_scope(TenantScope(tenant_id="other")), valid_id),)
                    )
                ),
            ),
            pytest.raises(LearningScopeError, match="out-of-scope source"),
        ):
            await retriever.retrieve(request)

        with (
            patch.object(
                engine,
                "retrieve",
                AsyncMock(return_value=fake_response((FakeEvidence(valid_scope, "invalid"),))),
            ),
            pytest.raises(LearningScopeError, match="invalid artifact source"),
        ):
            await retriever.retrieve(request)
    finally:
        await engine.close()


async def test_retrievalloom_retrieval_enforces_result_limit_and_context_bound() -> None:
    repository = InMemoryArtifactRepository()
    first = await active_artifact(repository, ArtifactScope(tenant_id="tenant", agent_id="agent"))
    second = await active_artifact(
        repository, ArtifactScope(tenant_id="tenant", agent_id="agent"), content="Other lesson"
    )
    request = RetrievalRequest(
        scope=TenantScope(tenant_id="tenant"),
        agent_id="agent",
        task="validate",
        limit=1,
    )
    scope = _retrieval_scope(request.scope)
    response = fake_response(
        (
            FakeEvidence(scope, _document_id(first.artifact_id, first.version)),
            FakeEvidence(scope, _document_id(second.artifact_id, second.version)),
        )
    )
    engine = RetrievalEngine(
        EngineConfig(
            lifecycle=LifecycleConfig(background=False),
            retrieval=RetrievalConfig(
                default_tenant_policy=TenantPolicy(max_top_k=1000, max_candidate_k=1000)
            ),
        )
    )
    retriever = RetrievalLoomRetriever(repository, engine=engine)
    await engine.initialize()
    retriever._indexed_scopes.add(scope.key)
    try:
        with patch.object(engine, "retrieve", AsyncMock(return_value=response)):
            limited = await retriever.retrieve(request)
        assert len(limited.results) == 1
        assert limited.truncated

        with patch.object(engine, "retrieve", AsyncMock(return_value=response)):
            bounded = await retriever.retrieve(
                request.model_copy(update={"limit": 10, "max_context_chars": 5})
            )
        assert not bounded.results
        assert bounded.truncated
    finally:
        await engine.close()


async def test_retrievalloom_rechecks_artifact_applicability_validation_and_assessment() -> None:
    repository = InMemoryArtifactRepository()
    artifact = await active_artifact(
        repository, ArtifactScope(tenant_id="tenant", agent_id="alice")
    )
    scope = _retrieval_scope(TenantScope(tenant_id="tenant"))
    response = fake_response(
        (FakeEvidence(scope, _document_id(artifact.artifact_id, artifact.version)),)
    )
    engine = RetrievalEngine(
        EngineConfig(
            lifecycle=LifecycleConfig(background=False),
            retrieval=RetrievalConfig(
                default_tenant_policy=TenantPolicy(max_top_k=1000, max_candidate_k=1000)
            ),
        )
    )
    request = RetrievalRequest(
        scope=TenantScope(tenant_id="tenant"), agent_id="alice", task="validate"
    )
    retriever = RetrievalLoomRetriever(repository, engine=engine)
    await engine.initialize()
    retriever._indexed_scopes.add(scope.key)
    try:
        with (
            patch.object(engine, "retrieve", AsyncMock(return_value=response)),
            patch.object(
                repository, "get", AsyncMock(side_effect=ArtifactNotFoundError("missing"))
            ),
        ):
            assert not (await retriever.retrieve(request)).results

        foreign = await active_artifact(
            repository, ArtifactScope(tenant_id="foreign", agent_id="alice")
        )
        with (
            patch.object(engine, "retrieve", AsyncMock(return_value=response)),
            patch.object(repository, "get", AsyncMock(return_value=foreign)),
            pytest.raises(LearningScopeError, match="out-of-scope result"),
        ):
            await retriever.retrieve(request)

        with patch.object(engine, "retrieve", AsyncMock(return_value=response)):
            assert not (
                await retriever.retrieve(request.model_copy(update={"agent_id": "bob"}))
            ).results
            assert not (
                await retriever.retrieve(
                    request.model_copy(update={"artifact_types": (ArtifactType.KNOWLEDGE,)})
                )
            ).results
            assert not (
                await retriever.retrieve(request.model_copy(update={"min_confidence": 1.0}))
            ).results

        with (
            patch.object(engine, "retrieve", AsyncMock(return_value=response)),
            patch.object(retriever._guard, "current_and_assessed", AsyncMock(return_value=False)),
        ):
            assert not (await retriever.retrieve(request)).results
    finally:
        await engine.close()


async def test_retrievalloom_retrieve_drains_atomic_projection_intents() -> None:
    repository = InMemoryArtifactRepository()
    request = RetrievalRequest(scope=TenantScope(tenant_id="tenant"), task="find guidance")
    retriever = RetrievalLoomRetriever(repository)
    try:
        await retriever._ensure_open()
        assert retriever.engine is not None
        with (
            patch.object(repository, "atomic_projection_intents", True),
            patch.object(repository, "pending_projections", AsyncMock(return_value=())),
            patch.object(retriever.engine, "retrieve", AsyncMock(return_value=fake_response(()))),
        ):
            result = await retriever.retrieve(request)
        assert not result.results
    finally:
        await retriever.aclose()


async def test_retrievalloom_concurrent_retrieval_rechecks_projection_lock() -> None:
    repository = InMemoryArtifactRepository()
    request = RetrievalRequest(scope=TenantScope(tenant_id="tenant"), task="find guidance")
    retriever = RetrievalLoomRetriever(repository)
    try:
        await retriever._ensure_open()
        assert retriever.engine is not None
        retriever._indexed_scopes.clear()

        async def delayed_query(query: object) -> ArtifactPage:
            await asyncio.sleep(0)
            return ArtifactPage(artifacts=())

        with (
            patch.object(repository, "query", delayed_query),
            patch.object(retriever.engine, "retrieve", AsyncMock(return_value=fake_response(()))),
        ):
            results = await asyncio.gather(retriever.retrieve(request), retriever.retrieve(request))
        assert all(not result.results for result in results)
    finally:
        await retriever.aclose()


async def test_retrievalloom_preserves_evidence_and_filters_scope_and_visibility() -> None:
    repository = InMemoryArtifactRepository()
    private = await active_artifact(
        repository,
        ArtifactScope(
            tenant_id="tenant",
            agent_id="alice",
            task_type="research",
            tool="search",
        ),
    )
    shared = await active_artifact(
        repository,
        ArtifactScope(
            tenant_id="tenant",
            visibility=ScopeVisibility.TENANT,
            task_type="research",
            tool="search",
        ),
    )
    await active_artifact(
        repository,
        ArtifactScope(tenant_id="tenant", agent_id="bob", task_type="research", tool="search"),
    )
    await active_artifact(
        repository,
        ArtifactScope(tenant_id="tenant", agent_id="alice", task_type="research", tool="other"),
    )
    await active_artifact(
        repository,
        ArtifactScope(tenant_id="other", agent_id="alice", task_type="research", tool="search"),
    )

    retriever = RetrievalLoomRetriever(repository)
    await retriever.project(private)
    assert retriever.engine is None
    request = RetrievalRequest(
        scope=TenantScope(tenant_id="tenant"),
        agent_id="alice",
        task_type="research",
        tool="search",
        task="validate query results",
    )
    result = await retriever.retrieve(request)

    assert {item.artifact.artifact_id for item in result.results} == {
        private.artifact_id,
        shared.artifact_id,
    }
    assert result.retrieval_snapshot is not None
    assert all(item.engine_evidence for item in result.results)
    assert all(
        {"segments", "sources", "provenance"} <= evidence.keys()
        for item in result.results
        for evidence in item.engine_evidence
    )
    assert all(item.explanation for item in result.results)
    await retriever.aclose()


async def test_retrievalloom_rechecks_versions_and_rebuilds_from_the_repository() -> None:
    repository = InMemoryArtifactRepository()
    artifact = await active_artifact(
        repository, ArtifactScope(tenant_id="tenant", agent_id="agent")
    )
    retriever = RetrievalLoomRetriever(repository)
    request = RetrievalRequest(
        scope=TenantScope(tenant_id="tenant"), agent_id="agent", task="validate query"
    )
    assert (await retriever.retrieve(request)).artifacts == (artifact,)

    review = artifact.model_copy(
        update={
            "status": ArtifactStatus.UNDER_REVIEW,
            "content": "Verify updated outcomes",
            "version": artifact.version + 1,
            "updated_at": artifact.updated_at + timedelta(microseconds=1),
        }
    )
    review = await repository.update(review, expected_version=artifact.version)
    decision = ValidationRecord(
        reviewer="reviewer",
        approved=True,
        policy_version="default-v1",
        reviewed_version=review.version,
        artifact_digest=review.review_digest(),
    )
    validated = review.model_copy(
        update={
            "status": ArtifactStatus.VALIDATED,
            "version": review.version + 1,
            "updated_at": review.updated_at + timedelta(microseconds=1),
            "validation_history": (*review.validation_history, decision),
        }
    )
    validated = await repository.update(validated, expected_version=review.version)
    current = validated.model_copy(
        update={
            "status": ArtifactStatus.ACTIVE,
            "version": validated.version + 1,
            "updated_at": validated.updated_at + timedelta(microseconds=1),
        }
    )
    current = await repository.update(current, expected_version=validated.version)

    assert not (await retriever.retrieve(request)).results
    assert await retriever.reindex(request.scope) == 1
    updated = await retriever.retrieve(
        request.model_copy(update={"task": "verify updated outcomes"})
    )
    assert updated.artifacts == (current,)
    await retriever.aclose()


async def test_retrievalloom_rejects_blank_queries_and_keeps_caller_engine_open() -> None:
    repository = InMemoryArtifactRepository()
    retriever = RetrievalLoomRetriever(repository)
    with pytest.raises(UnsupportedCapabilityError, match="nonblank"):
        await retriever.retrieve(RetrievalRequest(scope=TenantScope(tenant_id="tenant")))
    request = RetrievalRequest(scope=TenantScope(tenant_id="tenant"), task="no matching artifact")
    assert not (await retriever.retrieve(request)).results
    owned_engine = retriever.engine
    assert owned_engine is not None
    await retriever.aclose()
    assert owned_engine.state is EngineState.CLOSED
    assert not (await retriever.retrieve(request)).results
    await retriever.aclose()

    engine = RetrievalEngine(
        EngineConfig(
            retrieval=RetrievalConfig(
                default_tenant_policy=TenantPolicy(max_top_k=1000, max_candidate_k=1000)
            ),
            lifecycle=LifecycleConfig(background=False),
        ),
    )
    injected = RetrievalLoomRetriever(repository, engine=engine)
    result = await injected.retrieve(request)
    assert not result.results
    await injected.aclose()
    assert engine.state is EngineState.OPEN
    await engine.close()
    with pytest.raises(UnsupportedCapabilityError, match="not usable"):
        await injected._ensure_open()


async def test_retrievalloom_graph_depth_expands_through_shared_applicability() -> None:
    repository = InMemoryArtifactRepository()
    scope = ArtifactScope(tenant_id="tenant", agent_id="agent", tool="ledger-api")
    direct = await active_artifact(repository, scope, content="Reconcile the ledger first")
    related = await active_artifact(repository, scope, content="Escalate stale balances")
    retriever = RetrievalLoomRetriever(repository, graph_depth=1)
    request = RetrievalRequest(
        scope=TenantScope(tenant_id="tenant"),
        agent_id="agent",
        tool="ledger-api",
        task="ledger api",
    )

    flat = await retriever.retrieve(request)
    expanded = await retriever.retrieve(request.model_copy(update={"graph_depth": 1}))

    assert related.artifact_id not in {item.artifact_id for item in flat.artifacts}
    assert {item.artifact_id for item in expanded.artifacts} == {
        direct.artifact_id,
        related.artifact_id,
    }
    graph_hits = [
        hit
        for item in expanded.results
        if item.artifact.artifact_id == related.artifact_id
        for evidence in item.engine_evidence
        for hit in cast("Any", evidence)["provenance"]["hits"]
    ]
    assert any(hit["method"]["channel"] == "graph" for hit in graph_hits)
    with pytest.raises(UnsupportedCapabilityError, match="configured as 1"):
        await retriever.retrieve(request.model_copy(update={"graph_depth": 2}))
    await retriever.aclose()


def test_retrievalloom_graph_links_an_artifact_to_the_one_it_supersedes() -> None:
    previous = uuid4()
    artifact = LearnedArtifact(
        artifact_type=ArtifactType.STRATEGY,
        title="Validate results",
        content="Validate query results",
        scope=ArtifactScope(tenant_id="tenant", agent_id="agent"),
        supersedes=previous,
    )

    graph = _graph_representation(artifact)

    assert f"artifact:{previous}" in {entity.key for entity in graph.entities}
    assert any(
        relationship.relation_type == "supersedes"
        and relationship.target_key == f"artifact:{previous}"
        for relationship in graph.relationships
    )


async def test_retrievalloom_graph_depth_fails_without_a_graph_channel() -> None:
    repository = InMemoryArtifactRepository()
    retriever = RetrievalLoomRetriever(repository)
    request = RetrievalRequest(
        scope=TenantScope(tenant_id="tenant"), task="validate", graph_depth=1
    )
    with pytest.raises(UnsupportedCapabilityError, match="graph channel"):
        await retriever.retrieve(request)
    await retriever.aclose()
    with pytest.raises(ValueError, match="between 0 and 4"):
        RetrievalLoomRetriever(repository, graph_depth=5)
    engine = RetrievalEngine(EngineConfig(lifecycle=LifecycleConfig(background=False)))
    with pytest.raises(ValueError, match="caller-owned"):
        RetrievalLoomRetriever(repository, engine=engine, graph_depth=1)


class DurableMemoryRepository(InMemoryArtifactRepository):
    durable = True


async def test_retrievalloom_projection_ttl_refreshes_only_shareable_owned_projections() -> None:
    with pytest.raises(ValueError, match="projection_ttl"):
        RetrievalLoomRetriever(InMemoryArtifactRepository(), projection_ttl=timedelta(0))
    scope = TenantScope(tenant_id="tenant")
    durable = DurableMemoryRepository()
    engine = RetrievalEngine(EngineConfig(lifecycle=LifecycleConfig(background=False)))
    retrievers = {
        "expiring": RetrievalLoomRetriever(durable, projection_ttl=timedelta(seconds=30)),
        "disabled": RetrievalLoomRetriever(durable, projection_ttl=None),
        "memory": RetrievalLoomRetriever(InMemoryArtifactRepository()),
        "caller-owned": RetrievalLoomRetriever(durable, engine=engine),
    }
    now = [0.0]
    for retriever in retrievers.values():
        retriever._clock = lambda: now[0]
        await retriever.reindex(scope)
    key = next(iter(retrievers["expiring"]._indexed_scopes))
    now[0] = 29.0
    assert all(retriever._fresh(key) for retriever in retrievers.values())
    now[0] = 30.0
    assert not retrievers["expiring"]._fresh(key)
    assert all(retrievers[name]._fresh(key) for name in ("disabled", "memory", "caller-owned"))
    for retriever in retrievers.values():
        await retriever.aclose()
    await engine.close()
