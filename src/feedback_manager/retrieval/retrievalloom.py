"""RetrievalLoom-backed discovery of authoritative learned artifacts."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from feedback_manager.learning.errors import (
    ArtifactNotFoundError,
    LearningScopeError,
    UnsupportedCapabilityError,
)
from feedback_manager.learning.models import (
    ArtifactScope,
    ArtifactStatus,
    LearnedArtifact,
    TenantScope,
)
from feedback_manager.learning.scope import is_applicable
from feedback_manager.learning.validation import DefaultLearningValidationPolicy
from feedback_manager.observability.operations import operation
from feedback_manager.retrieval.base import LearnedArtifactRetriever
from feedback_manager.retrieval.models import RetrievalRequest, RetrievalResult, RetrievedArtifact
from feedback_manager.retrieval.structured import StructuredRetriever
from feedback_manager.storage.artifacts.base import ArtifactQuery, ArtifactRepository

if TYPE_CHECKING:
    from retrievalloom import FilterExpression, GraphRepresentation, RetrievalEngine, Scope
    from retrievalloom.domain.inputs import Representation
    from retrievalloom.domain.retrieval import Evidence as LoomEvidence

    from feedback_manager.learning.assessment_verification import AssessmentVerifier
    from feedback_manager.learning.validation import LearningValidationPolicy
    from feedback_manager.observability.operations import OperationObserver

_COLLECTION = "learned-artifacts"
_DOCUMENT_PREFIX = "feedback-manager:artifact:"
_PAGE_SIZE = 1000
# RetrievalLoom's GraphChannelConfig accepts depths from 1 through 4.
_MAX_GRAPH_DEPTH = 4
_DEFAULT_PROJECTION_TTL = timedelta(seconds=30)
# Matches RetrievalLoom's word tokenizer so identifiers such as "ledger-api" seed graph queries.
_WORD = re.compile(r"\w+", re.UNICODE)


def _create_owned_engine(graph_depth: int = 0) -> RetrievalEngine:
    from resourcera import ResourceConfig
    from retrievalloom import (
        ChannelsConfig,
        GraphChannelConfig,
        IndexChannelConfig,
        LifecycleConfig,
        MethodConfig,
        OffloaderConfig,
        RerankingConfig,
        RetrievalConfig,
        TenantPolicy,
        create_retrieval_engine,
    )

    return create_retrieval_engine(
        lifecycle=LifecycleConfig(background=False),
        retrieval=RetrievalConfig(
            channels=ChannelsConfig(
                index=IndexChannelConfig(dense=MethodConfig(enabled=False)),
                graph=(
                    GraphChannelConfig(enabled=True, depth=graph_depth)
                    if graph_depth
                    else GraphChannelConfig()
                ),
            ),
            reranking=RerankingConfig(enabled=False),
            default_tenant_policy=TenantPolicy(max_top_k=1000, max_candidate_k=1000),
        ),
        offloader=OffloaderConfig(
            resource_config=ResourceConfig(
                cpu_budget_fraction=1.0,
                managed_state_budget_bytes=256 * 1024 * 1024,
            )
        ),
    )


@dataclass(slots=True)
class _CandidateAccumulator:
    artifact: LearnedArtifact
    evidence: dict[str, LoomEvidence] = field(default_factory=dict)
    score: float = 0.0


class RetrievalLoomRetriever(LearnedArtifactRetriever):
    """Use RetrievalLoom for discovery, then revalidate each authoritative artifact.

    The artifact repository remains authoritative. Its current active snapshots are
    projected into RetrievalLoom, and every hit is checked for current version,
    scope, applicability, assessment and review eligibility before it is returned.

    Graph expansion uses RetrievalLoom's native graph channel, whose depth is fixed
    by engine configuration. ``graph_depth`` configures the owned engine; a
    caller-owned engine supplies its own ``GraphChannelConfig``. A request's
    nonzero ``graph_depth`` must equal the engine's configured depth, and zero
    disables the graph channel for that request.

    The owned engine keeps its projection in this process's memory. Over a
    durable artifact repository, which other processes may also write, each
    scope is rebuilt from authoritative records once it is older than
    ``projection_ttl``; ``None`` disables the refresh for single-process hosts.
    Retrieval revalidates every hit, so a stale projection can omit an artifact
    for at most ``projection_ttl`` but never return a superseded one. A
    caller-owned engine is kept current by RetrievalLoom itself: share its
    metadata store between engines and run its lifecycle rounds.
    """

    def __init__(
        self,
        repository: ArtifactRepository,
        *,
        engine: RetrievalEngine | None = None,
        graph_depth: int = 0,
        projection_ttl: timedelta | None = _DEFAULT_PROJECTION_TTL,
        validation_policy: LearningValidationPolicy | None = None,
        assessment_verifier: AssessmentVerifier | None = None,
        observer: OperationObserver | None = None,
    ) -> None:
        if not 0 <= graph_depth <= _MAX_GRAPH_DEPTH:
            raise ValueError(f"graph_depth must be between 0 and {_MAX_GRAPH_DEPTH}")
        if projection_ttl is not None and projection_ttl <= timedelta(0):
            raise ValueError("projection_ttl must be positive or None")
        if engine is not None and graph_depth:
            raise ValueError("configure graph depth on the caller-owned RetrievalLoom engine")
        self.repository = repository
        self._graph_depth = graph_depth
        self._owns_engine = engine is None
        self.projection_ttl = projection_ttl
        self._clock = time.monotonic
        self.engine = engine
        self.observer = observer
        self.validation_policy = (
            validation_policy
            if validation_policy is not None
            else DefaultLearningValidationPolicy()
        )
        self._guard = StructuredRetriever(
            repository,
            validation_policy=self.validation_policy,
            assessment_verifier=assessment_verifier,
        )
        self._initialization_lock = asyncio.Lock()
        self._projection_lock = asyncio.Lock()
        self._indexed_scopes: set[str] = set()
        self._indexed_artifacts: dict[str, set[UUID]] = {}
        self._indexed_at: dict[str, float] = {}

    async def aclose(self) -> None:
        """Close only the engine created and owned by this retriever."""
        if not self._owns_engine or self.engine is None:
            return
        from retrievalloom import EngineState

        async with self._initialization_lock:
            if self.engine.state is not EngineState.CLOSED:
                await self.engine.close()
            self._indexed_scopes.clear()
            self._indexed_artifacts.clear()

    async def index(self, artifact: LearnedArtifact) -> None:
        """Refresh one artifact's current searchable projection."""
        await self._ensure_open()
        async with self._projection_lock:
            await self._index(artifact)

    async def project(self, artifact: LearnedArtifact) -> None:
        """Invalidate a scope projection so retrieval can rebuild it from current records."""
        if self.repository.atomic_projection_intents:
            return
        key = _retrieval_scope(artifact.scope).key
        self._indexed_scopes.discard(key)
        self._indexed_artifacts.pop(key, None)

    async def drain_intents(self, scope: TenantScope, *, limit: int = 100) -> int:
        """Apply and acknowledge one bounded page of atomic artifact projection intents."""
        if not self.repository.atomic_projection_intents:
            raise UnsupportedCapabilityError("artifact repository has no projection outbox")
        if not 1 <= limit <= 1000:
            raise ValueError("projection limit must be between 1 and 1000")
        await self._ensure_open()
        engine = self._require_engine()
        retrieval_scope = _retrieval_scope(scope)
        async with self._projection_lock:
            intents = await self.repository.pending_projections(scope, limit=limit)
            if len(intents) > limit:
                raise LearningScopeError("projection outbox exceeded the requested page size")
            for intent in intents:
                if (
                    intent.scope.tenant_id != scope.tenant_id
                    or intent.scope.namespace != scope.namespace
                ):
                    raise LearningScopeError("projection outbox returned an out-of-scope intent")
                try:
                    artifact = await self.repository.get(scope, intent.artifact_id)
                except ArtifactNotFoundError:
                    await _delete_versions(
                        engine,
                        retrieval_scope,
                        intent.artifact_id,
                        intent.artifact_version,
                    )
                else:
                    if (
                        artifact.scope.tenant_id != scope.tenant_id
                        or artifact.scope.namespace != scope.namespace
                    ):
                        raise LearningScopeError(
                            "artifact repository returned an out-of-scope result"
                        )
                    await self._index(artifact)
                await self.repository.acknowledge_projection(scope, intent.intent_id)
            return len(intents)

    async def reindex(self, scope: TenantScope) -> int:
        """Rebuild one tenant namespace from authoritative artifact snapshots."""
        await self._ensure_open()
        retrieval_scope = _retrieval_scope(scope)
        async with self._projection_lock:
            active_artifacts = await self._rebuild(scope, retrieval_scope)
        return len(active_artifacts)

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        """Return ranked, reviewed artifacts and preserve RetrievalLoom evidence."""
        if not request.task.strip():
            raise UnsupportedCapabilityError("RetrievalLoom requires a nonblank retrieval task")
        await self._ensure_open()
        graph_enabled, configured_depth = self._graph_channel()
        if request.graph_depth and not graph_enabled:
            raise UnsupportedCapabilityError(
                "graph_depth requires a RetrievalLoom engine with the graph channel enabled"
            )
        if request.graph_depth and request.graph_depth != configured_depth:
            raise UnsupportedCapabilityError(
                f"RetrievalLoom graph depth is configured as {configured_depth}; "
                f"request graph_depth {request.graph_depth} cannot be honored"
            )
        scope = _retrieval_scope(request.scope)
        with operation(self.observer, "learning.retrieval"):
            if self.repository.atomic_projection_intents:
                await self.drain_intents(request.scope)
            if not self._fresh(scope.key):
                async with self._projection_lock:
                    if not self._fresh(scope.key):
                        await self._rebuild(request.scope, scope)
            from retrievalloom import ResultStatus, RetrievalExecutionConfig

            response = await self._require_engine().retrieve(
                request.task,
                scope=scope,
                filters=_filters(request),
                top_k=request.max_candidates,
                config=RetrievalExecutionConfig(
                    include_candidates=True,
                    graph=bool(request.graph_depth) if graph_enabled else None,
                ),
            )
            if len(response.evidence) > request.max_candidates:
                raise LearningScopeError("RetrievalLoom exceeded the requested candidate bound")
            grouped: dict[UUID, _CandidateAccumulator] = {}
            for evidence in response.evidence:
                for source in evidence.sources:
                    if source.scope != scope:
                        raise LearningScopeError("RetrievalLoom returned an out-of-scope source")
                    identity = _parse_document_id(source.document_id)
                    if identity is None:
                        raise LearningScopeError(
                            "RetrievalLoom returned an invalid artifact source"
                        )
                    artifact_id, version = identity
                    artifact = await self._current(request, artifact_id, version)
                    if artifact is None:
                        continue
                    entry = grouped.setdefault(artifact_id, _CandidateAccumulator(artifact))
                    entry.evidence[evidence.evidence_id] = evidence
                    entry.score = max(entry.score, evidence.score)

            candidates = [self._candidate(entry) for entry in grouped.values()]
            candidates.sort(
                key=lambda item: (-item.relevance_score, str(item.artifact.artifact_id))
            )
            selected: list[RetrievedArtifact] = []
            context_size = 0
            truncated = (
                response.status is ResultStatus.PARTIAL
                or len(response.evidence) == request.max_candidates
            )
            for candidate in candidates:
                if len(selected) == request.limit:
                    truncated = True
                    break
                size = len(candidate.artifact.title) + 1 + len(candidate.artifact.content)
                if selected:
                    size += 2
                if context_size + size > request.max_context_chars:
                    truncated = True
                    continue
                selected.append(candidate)
                context_size += size
            return RetrievalResult(
                results=tuple(selected),
                truncated=truncated,
                retrieval_snapshot=response.snapshot.model_dump(mode="json"),
                retrieval_warnings=tuple(
                    warning.model_dump(mode="json") for warning in response.warnings
                ),
            )

    async def _ensure_open(self) -> None:
        from retrievalloom import EngineState

        async with self._initialization_lock:
            if self.engine is None or (
                self.engine.state is EngineState.CLOSED and self._owns_engine
            ):
                self.engine = _create_owned_engine(self._graph_depth)
                self._indexed_scopes.clear()
                self._indexed_artifacts.clear()
            engine = self._require_engine()
            if engine.state is EngineState.NEW:
                await engine.initialize()
            elif engine.state is not EngineState.OPEN:
                raise UnsupportedCapabilityError(
                    f"RetrievalLoom engine is not usable: {engine.state}"
                )

    def _require_engine(self) -> RetrievalEngine:
        if self.engine is None:
            raise UnsupportedCapabilityError("RetrievalLoom engine is not initialized")
        return self.engine

    def _graph_channel(self) -> tuple[bool, int]:
        graph = self._require_engine().config.retrieval.channels.graph
        return graph.enabled, graph.depth

    def _fresh(self, key: str) -> bool:
        if key not in self._indexed_scopes:
            return False
        if not self._owns_engine or self.projection_ttl is None or not self.repository.durable:
            return True
        return self._clock() - self._indexed_at[key] < self.projection_ttl.total_seconds()

    async def _rebuild(self, tenant_scope: TenantScope, scope: Scope) -> set[UUID]:
        # Commits racing the rebuild are covered by the next refresh, not missed.
        started = self._clock()
        active_artifacts = await self._reindex(tenant_scope, scope)
        self._indexed_scopes.add(scope.key)
        self._indexed_artifacts[scope.key] = active_artifacts
        self._indexed_at[scope.key] = started
        return active_artifacts

    async def _reindex(self, tenant_scope: TenantScope, scope: Scope) -> set[UUID]:
        cursor = None
        seen: set[UUID] = set()
        active: set[UUID] = set()
        while True:
            page = await self.repository.query(
                ArtifactQuery(scope=tenant_scope, limit=_PAGE_SIZE, cursor=cursor)
            )
            if len(page.artifacts) > _PAGE_SIZE:
                raise LearningScopeError("artifact repository exceeded the requested page size")
            for artifact in page.artifacts:
                if artifact.artifact_id in seen:
                    raise LearningScopeError("artifact repository returned a duplicate artifact")
                if (
                    artifact.scope.tenant_id != scope.tenant_id
                    or artifact.scope.namespace != scope.namespace
                ):
                    raise LearningScopeError("artifact repository returned an out-of-scope result")
                seen.add(artifact.artifact_id)
                if artifact.status is ArtifactStatus.ACTIVE:
                    active.add(artifact.artifact_id)
                await self._index(artifact)
            if page.next_cursor is None:
                return active
            if not page.artifacts or page.next_cursor == cursor:
                raise LearningScopeError("artifact repository returned an invalid cursor")
            cursor = page.next_cursor

    async def _index(self, artifact: LearnedArtifact) -> None:
        from retrievalloom import TextRepresentation
        from retrievalloom.domain.inputs import DocumentInput

        engine = self._require_engine()
        scope = _retrieval_scope(artifact.scope)
        document_id = _document_id(artifact.artifact_id, artifact.version)
        if artifact.status is not ArtifactStatus.ACTIVE:
            await _delete_versions(engine, scope, artifact.artifact_id, artifact.version)
            self._indexed_artifacts.get(scope.key, set()).discard(artifact.artifact_id)
            return
        await _delete_versions(engine, scope, artifact.artifact_id, artifact.version - 1)
        representations: list[Representation] = [
            TextRepresentation(text=f"{artifact.title}\n{artifact.description}\n{artifact.content}")
        ]
        if self._graph_channel()[0]:
            representations.append(_graph_representation(artifact))
        report = await engine.ingest(
            [
                DocumentInput(
                    _id=document_id,
                    scope=scope,
                    metadata=_metadata(artifact),
                    representations=representations,
                )
            ],
            scope=scope,
        )
        if any(document.error is not None for document in report.documents):
            raise LearningScopeError("RetrievalLoom could not index the current artifact")
        if scope.key in self._indexed_scopes:
            self._indexed_artifacts.setdefault(scope.key, set()).add(artifact.artifact_id)

    async def _current(
        self, request: RetrievalRequest, artifact_id: UUID, version: int
    ) -> LearnedArtifact | None:
        try:
            artifact = await self.repository.get(request.scope, artifact_id)
        except ArtifactNotFoundError:
            return None
        if (
            artifact.scope.tenant_id != request.scope.tenant_id
            or artifact.scope.namespace != request.scope.namespace
        ):
            raise LearningScopeError("artifact repository returned an out-of-scope result")
        if artifact.version != version or artifact.status is not ArtifactStatus.ACTIVE:
            return None
        if not is_applicable(
            artifact.scope,
            request.scope,
            agent_id=request.agent_id,
            agent_type=request.agent_type,
            task_type=request.task_type,
            tool=request.tool,
            workflow=request.workflow,
        ):
            return None
        if request.artifact_types and artifact.artifact_type not in request.artifact_types:
            return None
        decision = self.validation_policy.evaluate(artifact)
        if not decision.eligible or decision.confidence.confidence < request.min_confidence:
            return None
        if not await self._guard.current_and_assessed(artifact):
            return None
        return artifact

    def _candidate(self, entry: _CandidateAccumulator) -> RetrievedArtifact:
        artifact = entry.artifact
        evidence = tuple(entry.evidence.values())
        decision = self.validation_policy.evaluate(artifact)
        score = min(1.0, max(0.0, entry.score))
        details = [f"RetrievalLoom evidence score: {float(item.score):.6f}" for item in evidence]
        details.extend(
            f"{hit.method.key} raw score: {hit.raw_score}"
            for item in evidence
            for hit in item.provenance.hits
        )
        return RetrievedArtifact(
            artifact=artifact,
            relevance_score=score,
            confidence_score=decision.confidence.confidence,
            evidence_count=artifact.evidence_count,
            explanation=tuple(details),
            engine_evidence=tuple(item.model_dump(mode="json") for item in evidence),
        )


def _retrieval_scope(scope: TenantScope | ArtifactScope) -> Scope:
    from retrievalloom import Scope

    return Scope(
        tenant_id=scope.tenant_id,
        namespace=scope.namespace,
        collection=_COLLECTION,
    )


def _document_id(artifact_id: UUID, version: int) -> str:
    return f"{_DOCUMENT_PREFIX}{artifact_id}:v{version}"


def _parse_document_id(document_id: str) -> tuple[UUID, int] | None:
    if not document_id.startswith(_DOCUMENT_PREFIX):
        return None
    artifact_text, separator, version_text = document_id[len(_DOCUMENT_PREFIX) :].rpartition(":v")
    if not separator:
        return None
    try:
        artifact_id = UUID(artifact_text)
        version = int(version_text)
    except ValueError:
        return None
    return (artifact_id, version) if version > 0 else None


def _metadata(artifact: LearnedArtifact) -> dict[str, str | int | None]:
    return {
        "artifact_status": artifact.status.value,
        "artifact_type": artifact.artifact_type.value,
        "visibility": artifact.scope.visibility.value,
        "agent_id": artifact.scope.agent_id,
        "agent_type": artifact.scope.agent_type,
        "task_type": artifact.scope.task_type,
        "tool": artifact.scope.tool,
        "workflow": artifact.scope.workflow,
    }


def _graph_representation(artifact: LearnedArtifact) -> GraphRepresentation:
    """Describe an artifact's applicability as RetrievalLoom graph input.

    RetrievalLoom merges entities by type and key across documents, so shared
    task type, tool and workflow entities connect artifacts that apply to the same
    context, while each artifact keeps its own identity.
    """
    from retrievalloom import GraphEntityInput, GraphRelationshipInput, GraphRepresentation

    artifact_key = f"artifact:{artifact.artifact_id}"
    entities = [GraphEntityInput(key=artifact_key, name=artifact.title, entity_type="artifact")]
    relationships = []
    for field_name in ("task_type", "tool", "workflow"):
        value = getattr(artifact.scope, field_name)
        if value is None:
            continue
        key = f"{field_name}:{value}"
        words = " ".join(_WORD.findall(value))
        entities.append(
            GraphEntityInput(
                key=key,
                name=value,
                entity_type=field_name,
                aliases=(words,) if words and words != value else (),
            )
        )
        relationships.append(
            GraphRelationshipInput(
                source_key=artifact_key, target_key=key, relation_type=f"applies_to_{field_name}"
            )
        )
    if artifact.supersedes is not None:
        key = f"artifact:{artifact.supersedes}"
        entities.append(GraphEntityInput(key=key, name=key, entity_type="artifact"))
        relationships.append(
            GraphRelationshipInput(
                source_key=artifact_key, target_key=key, relation_type="supersedes"
            )
        )
    return GraphRepresentation(
        representation_id="graph",
        entities=tuple(entities),
        relationships=tuple(relationships),
    )


def _filters(request: RetrievalRequest) -> FilterExpression:
    from retrievalloom import And, Eq, In, Or

    visibility: list[FilterExpression] = [Eq(field="visibility", value="tenant")]
    if request.agent_id is not None:
        visibility.append(
            And(
                clauses=(
                    Eq(field="visibility", value="agent"),
                    Eq(field="agent_id", value=request.agent_id),
                )
            )
        )
    if request.agent_type is not None:
        visibility.append(
            And(
                clauses=(
                    Eq(field="visibility", value="agent_type"),
                    Eq(field="agent_type", value=request.agent_type),
                )
            )
        )
    clauses: list[FilterExpression] = [
        Eq(field="artifact_status", value=ArtifactStatus.ACTIVE.value),
        Or(clauses=tuple(visibility)),
    ]
    if request.artifact_types:
        clauses.append(
            In(
                field="artifact_type",
                values=tuple(item.value for item in request.artifact_types),
            )
        )
    for key in ("task_type", "tool", "workflow"):
        expected = getattr(request, key)
        clauses.append(
            Eq(field=key, value=expected)
            if expected is None
            else Or(
                clauses=(
                    Eq(field=key, value=None),
                    Eq(field=key, value=expected),
                )
            )
        )
    return And(clauses=tuple(clauses))


async def _delete_versions(
    engine: RetrievalEngine, scope: Scope, artifact_id: UUID, through_version: int
) -> None:
    for start in range(1, through_version + 1, _PAGE_SIZE):
        document_ids = [
            _document_id(artifact_id, version)
            for version in range(start, min(start + _PAGE_SIZE, through_version + 1))
        ]
        await engine.delete(document_ids, scope=scope)


__all__ = ["RetrievalLoomRetriever"]
