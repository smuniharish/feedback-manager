"""Conformance test kits for your own feedback-manager components.

Subclass a contract in a test module, implement its factory, and your test
runner collects one test per promise the component makes:

```python
from feedback_manager.testing import FeedbackStoreContract


class TestMyStore(FeedbackStoreContract):
    async def make_store(self):
        store = await MyStore.open(dsn)
        await store.truncate()  # every test starts from an empty store
        return store
```

Every contract follows the same rules:

- Each test calls the factory for a new, empty component, runs one scenario on
  it, and then releases it with ``aclose()`` or ``close()`` when it has one.
  Override ``release`` to clean up differently.
- Tests are plain synchronous methods that raise `AssertionError` with a
  descriptive message, so they run under pytest, or under unittest when the
  subclass also inherits ``unittest.TestCase``. Neither pytest nor an async
  plugin is required.
- Scenarios run with ``asyncio.run``; override ``run_scenario`` to use another
  event loop.

The agxai provenance store contract lives in ``agxai.testing``.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import math
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from feedback_manager.assessment import (
    AssessmentEvidence,
    AssessmentStatus,
    FeedbackAssessment,
    LearningDecision,
)
from feedback_manager.contracts.store import FeedbackQuery, FeedbackStore
from feedback_manager.core.events import FeedbackEvent
from feedback_manager.core.status import FeedbackStatus
from feedback_manager.core.targets import FeedbackTarget
from feedback_manager.embeddings.base import EmbeddingProvider
from feedback_manager.embeddings.models import EmbeddingSpace
from feedback_manager.errors import (
    FeedbackConflictError,
    FeedbackLifecycleError,
    FeedbackNotFoundError,
    FeedbackStoreError,
)
from feedback_manager.experience import ExecutionExperience
from feedback_manager.learning.errors import (
    ArtifactConflictError,
    ArtifactNotFoundError,
    LearningValidationError,
)
from feedback_manager.learning.jobs import (
    LearningJob,
    LearningJobNotFoundError,
    LearningJobStatus,
    LearningJobStore,
    LearningLeaseLostError,
)
from feedback_manager.learning.models import (
    ArtifactScope,
    ArtifactStatus,
    ArtifactType,
    LearnedArtifact,
    TenantScope,
)
from feedback_manager.storage.artifacts.base import ArtifactQuery, ArtifactRepository
from feedback_manager.storage.assessment import (
    AssessmentConflictError,
    AssessmentNotFoundError,
    AssessmentRepositoryABC,
)
from feedback_manager.storage.experience import (
    ExperienceConflictError,
    ExperienceNotFoundError,
    ExperienceRepositoryABC,
)
from feedback_manager.storage.graph.base import (
    GraphCapabilities,
    GraphDirection,
    GraphNode,
    GraphRelationship,
    GraphStore,
)
from feedback_manager.storage.payload import (
    PayloadConflictError,
    PayloadNotFoundError,
    PayloadScope,
    PayloadStoreABC,
)
from feedback_manager.storage.vector.base import (
    VectorCapabilities,
    VectorRecord,
    VectorStore,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Iterable

__all__ = [
    "ArtifactRepositoryContract",
    "AssessmentRepositoryContract",
    "EmbeddingProviderContract",
    "ExperienceRepositoryContract",
    "FeedbackStoreContract",
    "GraphStoreContract",
    "LearningJobStoreContract",
    "PayloadStoreContract",
    "VectorStoreContract",
]

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
_SCOPE = TenantScope(tenant_id="contract-tenant")
_OTHER_TENANT = TenantScope(tenant_id="contract-other")
_OTHER_NAMESPACE = TenantScope(tenant_id="contract-tenant", namespace="contract-private")
_HOSTILE = "a' OR '1'='1 //"
_SPACE = EmbeddingSpace(provider="contract", model="model", revision="1", dimensions=2)
_OTHER_SPACE = EmbeddingSpace(provider="contract", model="other", revision="1", dimensions=2)


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def _raises(
    error: type[BaseException] | tuple[type[BaseException], ...],
    call: Awaitable[object],
    message: str,
) -> None:
    try:
        await call
    except error:
        return
    raise AssertionError(message)


class _Contract[ComponentT](ABC):
    """Runs scenarios against a new component and releases it afterwards."""

    def run_scenario(self, scenario: Coroutine[Any, Any, None]) -> None:
        """Run one scenario to completion; the default is ``asyncio.run``."""
        asyncio.run(scenario)

    async def release(self, component: ComponentT) -> None:
        """Release a component after a test; calls ``aclose()`` or ``close()`` if present."""
        for name in ("aclose", "close"):
            close = getattr(component, name, None)
            if callable(close):
                result = close()
                if inspect.isawaitable(result):
                    await result
                return

    @abstractmethod
    async def _make(self) -> ComponentT: ...

    def _run(self, scenario: Callable[[ComponentT], Awaitable[None]]) -> None:
        async def body() -> None:
            component = await self._make()
            try:
                await scenario(component)
            finally:
                await self.release(component)

        self.run_scenario(body())


def _feedback(**overrides: Any) -> FeedbackEvent:
    fields: dict[str, Any] = {
        "source": "human",
        "category": "correction",
        "target": FeedbackTarget(type="generation", id="gen-1"),
        "status": FeedbackStatus.RECEIVED,
    }
    fields.update(overrides)
    return FeedbackEvent.model_validate(fields)


class FeedbackStoreContract(_Contract[FeedbackStore]):
    """Tests every promise a `FeedbackStore` makes to `FeedbackManager`."""

    @abstractmethod
    async def make_store(self) -> FeedbackStore:
        """Return a new, empty, ready-to-use store."""

    async def _make(self) -> FeedbackStore:
        return await self.make_store()

    def test_create_and_get_round_trip(self) -> None:
        """A created event reads back unchanged; unknown IDs read as ``None``."""

        async def scenario(store: FeedbackStore) -> None:
            event = _feedback(payload={"nested": {"score": 0.5, "tags": ["a"]}})
            created = await store.create(event)
            _check(created == event, f"create returned {created!r}, expected the event")
            fetched = await store.get(event.feedback_id)
            _check(fetched == event, f"get returned {fetched!r} for a created event")
            _check(await store.get(uuid4()) is None, "get must return None for an unknown ID")

        self._run(scenario)

    def test_duplicate_feedback_ids_are_rejected(self) -> None:
        """Creating a second event with the same ``feedback_id`` raises `FeedbackStoreError`."""

        async def scenario(store: FeedbackStore) -> None:
            event = await store.create(_feedback())
            await _raises(
                FeedbackStoreError,
                store.create(event),
                "creating a duplicate feedback_id must raise FeedbackStoreError",
            )

        self._run(scenario)

    def test_idempotency_keys_deduplicate_concurrent_creates(self) -> None:
        """Concurrent creates with one idempotency key store exactly one event."""

        async def scenario(store: FeedbackStore) -> None:
            results = await asyncio.gather(
                *(store.create(_feedback(idempotency_key="retry-1")) for _ in range(10))
            )
            stored = await store.query(FeedbackQuery())
            _check(
                len({event.feedback_id for event in results}) == 1,
                "every create with the same idempotency key must return the same event",
            )
            _check(len(stored) == 1, f"expected one stored event, found {len(stored)}")

        self._run(scenario)

    def test_transitions_are_compare_and_set(self) -> None:
        """Transitions apply only from ``expected`` and only along legal moves."""

        async def scenario(store: FeedbackStore) -> None:
            event = await store.create(_feedback())
            moved = await store.transition(
                event.feedback_id, FeedbackStatus.ACKNOWLEDGED, expected=FeedbackStatus.RECEIVED
            )
            _check(moved.status is FeedbackStatus.ACKNOWLEDGED, f"status is {moved.status!r}")
            _check(moved.updated_at > event.updated_at, "updated_at must move forward")
            _check(await store.get(event.feedback_id) == moved, "the transition was not stored")
            await _raises(
                FeedbackConflictError,
                store.transition(
                    event.feedback_id, FeedbackStatus.CANCELLED, expected=FeedbackStatus.RECEIVED
                ),
                "a stale expected status must raise FeedbackConflictError",
            )
            await _raises(
                FeedbackLifecycleError,
                store.transition(
                    event.feedback_id,
                    FeedbackStatus.RESOLVED,
                    expected=FeedbackStatus.ACKNOWLEDGED,
                ),
                "an illegal lifecycle move must raise FeedbackLifecycleError",
            )
            await _raises(
                FeedbackNotFoundError,
                store.transition(
                    uuid4(), FeedbackStatus.CANCELLED, expected=FeedbackStatus.RECEIVED
                ),
                "transitioning an unknown event must raise FeedbackNotFoundError",
            )

        self._run(scenario)

    def test_resolutions_are_recorded_once(self) -> None:
        """A repeated transition to the same status keeps the first resolution."""

        async def scenario(store: FeedbackStore) -> None:
            event = await store.create(_feedback())
            rejected = await store.transition(
                event.feedback_id,
                FeedbackStatus.REJECTED,
                expected=FeedbackStatus.RECEIVED,
                resolution={"reason": "duplicate"},
            )
            repeated = await store.transition(
                event.feedback_id,
                FeedbackStatus.REJECTED,
                expected=FeedbackStatus.REJECTED,
                resolution={"reason": "other"},
            )
            _check(
                rejected.resolution == repeated.resolution == {"reason": "duplicate"},
                f"resolutions were {rejected.resolution!r} and {repeated.resolution!r}",
            )

        self._run(scenario)

    def test_queries_filter_order_and_limit(self) -> None:
        """Queries match `FeedbackQuery.matches`, in creation order, bounded by ``limit``."""

        async def scenario(store: FeedbackStore) -> None:
            events = [
                _feedback(
                    source=source,
                    category=category,
                    target=FeedbackTarget(type=target_type, id=f"t-{index % 2}"),
                    correlation_id=f"run-{index % 3}",
                    idempotency_key=f"key-{index}",
                    created_at=_EPOCH + timedelta(minutes=index),
                )
                for index, (source, category, target_type) in enumerate(
                    [
                        ("human", "correction", "generation"),
                        ("tool", "timeout", "tool_call"),
                        ("human", "rating", "generation"),
                        ("evaluator", "quality", "run"),
                    ]
                )
            ]
            for event in events:
                await store.create(event)
            queries = [
                FeedbackQuery(),
                FeedbackQuery(source="human"),
                FeedbackQuery(category="rating"),
                FeedbackQuery(target_type="generation", target_id="t-0"),
                FeedbackQuery(correlation_id="run-1"),
                FeedbackQuery(idempotency_key="key-2"),
                FeedbackQuery(created_after=_EPOCH + timedelta(minutes=1)),
                FeedbackQuery(created_before=_EPOCH + timedelta(minutes=2), limit=1),
                FeedbackQuery(newest_first=True, limit=2),
            ]
            for query in queries:
                expected = [event for event in events if query.matches(event)]
                if query.newest_first:
                    expected.reverse()
                expected = expected[: query.limit]
                found = list(await store.query(query))
                _check(found == expected, f"{query!r} returned {found!r}, expected {expected!r}")

        self._run(scenario)


def _artifact(*, tenant: str = "contract-tenant", namespace: str = "default") -> LearnedArtifact:
    return LearnedArtifact(
        artifact_type=ArtifactType.STRATEGY,
        title="Validate before execution",
        content="Search, validate, then execute.",
        scope=ArtifactScope(tenant_id=tenant, namespace=namespace, agent_id="agent"),
        provenance={"context": {"value": "original"}},
        created_at=_EPOCH,
        updated_at=_EPOCH,
    )


def _next(artifact: LearnedArtifact, **changes: Any) -> LearnedArtifact:
    return artifact.model_copy(
        update={
            "version": artifact.version + 1,
            "updated_at": artifact.updated_at + timedelta(microseconds=1),
            **changes,
        }
    )


class ArtifactRepositoryContract(_Contract[ArtifactRepository]):
    """Tests every promise an `ArtifactRepository` makes to the learning service."""

    @abstractmethod
    async def make_repository(self) -> ArtifactRepository:
        """Return a new, empty, ready-to-use repository."""

    async def _make(self) -> ArtifactRepository:
        return await self.make_repository()

    def test_create_get_and_versions(self) -> None:
        """Versions append in order and every version stays readable."""

        async def scenario(repository: ArtifactRepository) -> None:
            artifact = _artifact()
            await repository.create(artifact)
            updated = await repository.update(
                _next(artifact, status=ArtifactStatus.UNDER_REVIEW), expected_version=1
            )
            _check(updated.version == 2, f"update returned version {updated.version}")
            history = await repository.versions(_SCOPE, artifact.artifact_id)
            _check([item.version for item in history] == [1, 2], f"versions were {history!r}")
            first = await repository.get(_SCOPE, artifact.artifact_id, version=1)
            _check(first.status is ArtifactStatus.CANDIDATE, "version 1 must be unchanged")
            current = await repository.get_many(_SCOPE, [artifact.artifact_id])
            _check([item.version for item in current] == [2], "get_many must return the latest")
            await repository.health()

        self._run(scenario)

    def test_identical_creates_are_idempotent_and_conflicts_are_rejected(self) -> None:
        """Recreating an identical candidate is a no-op; a different one conflicts."""

        async def scenario(repository: ArtifactRepository) -> None:
            artifact = _artifact()
            await repository.create(artifact)
            again = await repository.create(artifact)
            _check(again == artifact, f"recreating an identical artifact returned {again!r}")
            await _raises(
                ArtifactConflictError,
                repository.create(artifact.model_copy(update={"content": "different"})),
                "a different artifact with an existing ID must raise ArtifactConflictError",
            )
            await _raises(
                ArtifactConflictError,
                repository.create(_artifact().model_copy(update={"version": 2})),
                "new artifacts that are not version 1 must raise ArtifactConflictError",
            )

        self._run(scenario)

    def test_updates_use_optimistic_concurrency(self) -> None:
        """An update against a stale version raises `ArtifactConflictError`."""

        async def scenario(repository: ArtifactRepository) -> None:
            artifact = _artifact()
            await repository.create(artifact)
            reviewed = _next(artifact, status=ArtifactStatus.UNDER_REVIEW)
            await repository.update(reviewed, expected_version=1)
            await _raises(
                ArtifactConflictError,
                repository.update(_next(reviewed), expected_version=1),
                "a stale expected_version must raise ArtifactConflictError",
            )
            await _raises(
                ArtifactNotFoundError,
                repository.update(_next(_artifact()), expected_version=1),
                "updating an unknown artifact must raise ArtifactNotFoundError",
            )

        self._run(scenario)

    def test_tenants_and_namespaces_are_isolated(self) -> None:
        """Artifacts are invisible outside their tenant and namespace."""

        async def scenario(repository: ArtifactRepository) -> None:
            artifact = _artifact()
            await repository.create(artifact)
            for scope in (_OTHER_TENANT, _OTHER_NAMESPACE):
                await _raises(
                    ArtifactNotFoundError,
                    repository.get(scope, artifact.artifact_id),
                    f"an artifact leaked into {scope!r}",
                )
                page = await repository.query(ArtifactQuery(scope=scope))
                _check(page.artifacts == (), f"query in {scope!r} returned {page.artifacts!r}")
            await _raises(
                ArtifactNotFoundError,
                repository.get_many(_SCOPE, [artifact.artifact_id, uuid4()]),
                "get_many must reject a missing ID",
            )
            await _raises(
                ArtifactNotFoundError,
                repository.versions(_SCOPE, uuid4()),
                "versions of an unknown artifact must raise ArtifactNotFoundError",
            )

        self._run(scenario)

    def test_queries_filter_and_paginate(self) -> None:
        """Queries filter by status and type and page with ``next_cursor``."""

        async def scenario(repository: ArtifactRepository) -> None:
            created = [_artifact() for _ in range(3)]
            for artifact in created:
                await repository.create(artifact)
            await repository.update(
                _next(created[0], status=ArtifactStatus.UNDER_REVIEW), expected_version=1
            )
            seen: list[UUID] = []
            cursor: str | None = None
            while True:
                page = await repository.query(ArtifactQuery(scope=_SCOPE, limit=2, cursor=cursor))
                seen.extend(item.artifact_id for item in page.artifacts)
                cursor = page.next_cursor
                if cursor is None:
                    break
            expected = sorted(item.artifact_id for item in created)
            _check(sorted(seen) == expected, f"pagination returned {seen!r}")
            _check(len(seen) == len(set(seen)), "pagination returned an artifact twice")
            reviewing = await repository.query(
                ArtifactQuery(scope=_SCOPE, statuses=(ArtifactStatus.UNDER_REVIEW,))
            )
            _check(
                [item.artifact_id for item in reviewing.artifacts] == [created[0].artifact_id],
                f"the status filter returned {reviewing.artifacts!r}",
            )
            other_type = await repository.query(
                ArtifactQuery(scope=_SCOPE, artifact_types=(ArtifactType.KNOWLEDGE,))
            )
            _check(other_type.artifacts == (), "the type filter matched another type")

        self._run(scenario)


class PayloadStoreContract(_Contract[PayloadStoreABC]):
    """Tests the scoped payload storage contract."""

    @abstractmethod
    async def make_payload_store(self) -> PayloadStoreABC:
        """Return a new, empty, ready-to-use store."""

    async def _make(self) -> PayloadStoreABC:
        return await self.make_payload_store()

    def test_round_trip_metadata_and_deletion(self) -> None:
        """Payload bytes, metadata and deletion round-trip."""

        async def scenario(store: PayloadStoreABC) -> None:
            scope = PayloadScope(tenant_id="tenant", namespace="app", agent_id="agent")
            data = b'{"value":[1,2]}'
            record = await store.write(
                scope, data, content_type="application/json", serialization_version="1"
            )
            _check(record.scope == scope, "write returned the wrong scope")
            _check(record.digest == hashlib.sha256(data).hexdigest(), "wrong payload digest")
            _check(record.size == len(data), "wrong payload size")
            _check(record.content_type == "application/json", "wrong content type")
            _check(record.serialization_version == "1", "wrong serialization version")
            _check(await store.read(scope, record.payload_id) == data, "payload did not round-trip")
            _check(await store.metadata(scope, record.payload_id) == record, "metadata changed")
            _check(
                await store.delete(scope, record.payload_id), "delete did not remove the payload"
            )
            _check(
                not await store.delete(scope, record.payload_id), "repeated delete must be false"
            )
            await _raises(
                PayloadNotFoundError,
                store.read(scope, record.payload_id),
                "reading a deleted payload must raise PayloadNotFoundError",
            )
            await store.health()

        self._run(scenario)

    def test_identical_retries_and_conflicting_reuse(self) -> None:
        """Identical writes are idempotent and conflicting ID reuse is rejected."""

        async def scenario(store: PayloadStoreABC) -> None:
            scope = PayloadScope(tenant_id="tenant", namespace="app", agent_id="agent")
            payload_id = uuid4()
            results = await asyncio.gather(
                *(
                    store.write(
                        scope,
                        b"content",
                        content_type="text/plain",
                        serialization_version="1",
                        payload_id=payload_id,
                    )
                    for _ in range(5)
                )
            )
            _check(all(result == results[0] for result in results), "retries were not idempotent")
            await _raises(
                PayloadConflictError,
                store.write(
                    scope,
                    b"different",
                    content_type="text/plain",
                    serialization_version="1",
                    payload_id=payload_id,
                ),
                "different bytes with the same ID must conflict",
            )
            await _raises(
                PayloadConflictError,
                store.write(
                    scope,
                    b"content",
                    content_type="application/json",
                    serialization_version="1",
                    payload_id=payload_id,
                ),
                "different metadata with the same ID must conflict",
            )

        self._run(scenario)

    def test_tenants_namespaces_and_agents_are_isolated(self) -> None:
        """A payload cannot be read, inspected or deleted from another scope."""

        async def scenario(store: PayloadStoreABC) -> None:
            scope = PayloadScope(tenant_id="tenant", namespace="app", agent_id="agent")
            record = await store.write(
                scope, b"private", content_type="text/plain", serialization_version="1"
            )
            for other in (
                PayloadScope(tenant_id="other", namespace="app", agent_id="agent"),
                PayloadScope(tenant_id="tenant", namespace="other", agent_id="agent"),
                PayloadScope(tenant_id="tenant", namespace="app", agent_id="other"),
            ):
                await _raises(
                    PayloadNotFoundError,
                    store.read(other, record.payload_id),
                    "read must not cross payload scopes",
                )
                await _raises(
                    PayloadNotFoundError,
                    store.metadata(other, record.payload_id),
                    "metadata must not cross payload scopes",
                )
                _check(not await store.delete(other, record.payload_id), "delete crossed scopes")
            _check(await store.read(scope, record.payload_id) == b"private", "payload was deleted")

        self._run(scenario)


class AssessmentRepositoryContract(_Contract[AssessmentRepositoryABC]):
    """Tests the scoped, versioned assessment archive contract."""

    @abstractmethod
    async def make_assessment_repository(self) -> AssessmentRepositoryABC:
        """Return a new, empty, ready-to-use repository."""

    async def _make(self) -> AssessmentRepositoryABC:
        return await self.make_assessment_repository()

    def test_explicit_decisions_are_version_bound(self) -> None:
        """Only supported assessments can be accepted and decisions survive reassessment."""

        async def scenario(repository: AssessmentRepositoryABC) -> None:
            scope = PayloadScope(tenant_id="tenant", namespace="app", agent_id="agent")
            assessment = FeedbackAssessment(
                scope=scope,
                feedback_id=uuid4(),
                feedback_digest="a" * 64,
                experience_id=uuid4(),
                experience_version=1,
                experience_digest="b" * 64,
                status=AssessmentStatus.REVIEW_REQUIRED,
                method="contract",
                reviewer="critic",
                assessed_at=_EPOCH,
                supporting_evidence=(
                    AssessmentEvidence(
                        evidence_id="independent",
                        scope=scope,
                        experience_id=uuid4(),
                        experience_version=1,
                    ),
                ),
            )
            await repository.create(assessment)
            decision = LearningDecision(
                scope=scope,
                assessment_id=assessment.assessment_id,
                assessment_version=1,
                accepted=True,
                reviewer="host-reviewer",
                decided_at=_EPOCH + timedelta(seconds=1),
            )
            await _raises(
                AssessmentNotFoundError,
                repository.decide(decision.model_copy(update={"assessment_id": uuid4()})),
                "unknown assessment decisions must fail",
            )
            _check(
                await repository.decision(scope, assessment.assessment_id, assessment_version=1)
                is None,
                "undecided assessment must not have a decision",
            )
            await _raises(
                AssessmentConflictError,
                repository.decide(decision),
                "review-required assessments must not be accepted",
            )
            rejected = decision.model_copy(update={"accepted": False})
            _check(await repository.decide(rejected) == rejected, "rejection changed")
            _check(await repository.decide(rejected) == rejected, "decision retry changed")
            await _raises(
                AssessmentConflictError,
                repository.decide(rejected.model_copy(update={"reviewer": "other"})),
                "conflicting decisions must fail",
            )
            supported = assessment.model_copy(
                update={
                    "version": 2,
                    "status": AssessmentStatus.SUPPORTED,
                    "assessed_at": _EPOCH + timedelta(seconds=2),
                }
            )
            await repository.update(supported, expected_version=1)
            await _raises(
                AssessmentConflictError,
                repository.decide(rejected),
                "stale decisions cannot be accepted",
            )
            await _raises(
                AssessmentConflictError,
                repository.decide(
                    decision.model_copy(
                        update={
                            "assessment_version": 2,
                            "decided_at": _EPOCH + timedelta(seconds=3),
                        }
                    )
                ),
                "decision IDs cannot be reused",
            )
            accepted = decision.model_copy(
                update={
                    "decision_id": uuid4(),
                    "assessment_version": 2,
                    "decided_at": _EPOCH + timedelta(seconds=3),
                }
            )
            _check(await repository.decide(accepted) == accepted, "acceptance changed")
            _check(
                await repository.decision(scope, assessment.assessment_id, assessment_version=1)
                == rejected,
                "historical decision changed",
            )
            _check(
                await repository.decision(scope, assessment.assessment_id, assessment_version=2)
                == accepted,
                "current decision changed",
            )
            other = PayloadScope(tenant_id="other", namespace="app", agent_id="agent")
            await _raises(
                AssessmentNotFoundError,
                repository.decision(other, assessment.assessment_id, assessment_version=2),
                "decisions must be scoped",
            )

        self._run(scenario)

    def test_assessment_history_and_scope(self) -> None:
        """Version history is immutable, bounded, and scoped."""

        async def scenario(repository: AssessmentRepositoryABC) -> None:
            scope = PayloadScope(tenant_id="tenant", namespace="app", agent_id="agent")
            assessment = FeedbackAssessment(
                scope=scope,
                feedback_id=uuid4(),
                feedback_digest="a" * 64,
                experience_id=uuid4(),
                experience_version=1,
                experience_digest="b" * 64,
                status=AssessmentStatus.REVIEW_REQUIRED,
                method="contract",
                reviewer="contract-reviewer",
                assessed_at=_EPOCH,
            )
            _check(await repository.create(assessment) == assessment, "create changed assessment")
            await _raises(
                AssessmentConflictError,
                repository.create(
                    assessment.model_copy(update={"assessment_id": uuid4(), "version": 2})
                ),
                "new assessments must start at version one",
            )
            await _raises(
                AssessmentConflictError,
                repository.create(assessment.model_copy(update={"reviewer": "other"})),
                "conflicting create must fail",
            )
            updated = assessment.model_copy(
                update={
                    "version": 2,
                    "assessed_at": _EPOCH + timedelta(seconds=1),
                    "status": AssessmentStatus.SUPPORTED,
                }
            )
            _check(
                await repository.update(updated, expected_version=1) == updated,
                "update changed assessment",
            )
            _check(
                await repository.create(assessment) == updated,
                "create retry did not return current assessment",
            )
            _check(
                await repository.get(scope, assessment.assessment_id, version=1) == assessment,
                "first version changed",
            )
            _check(
                tuple(await repository.versions(scope, assessment.assessment_id, limit=1))
                == (assessment,),
                "first history page changed",
            )
            _check(
                tuple(
                    await repository.versions(
                        scope, assessment.assessment_id, after_version=1, limit=1
                    )
                )
                == (updated,),
                "second history page changed",
            )
            await _raises(
                AssessmentConflictError,
                repository.update(updated, expected_version=1),
                "stale updates must fail",
            )
            for other in (
                PayloadScope(tenant_id="other", namespace="app", agent_id="agent"),
                PayloadScope(tenant_id="tenant", namespace="other", agent_id="agent"),
                PayloadScope(tenant_id="tenant", namespace="app", agent_id="other"),
            ):
                await _raises(
                    AssessmentNotFoundError,
                    repository.get(other, assessment.assessment_id),
                    "assessment reads must not cross scopes",
                )
                _check(
                    not (await repository.for_feedback(other, assessment.feedback_id)).assessments,
                    "assessment query crossed scopes",
                )
            await repository.health()

        self._run(scenario)

    def test_assessment_feedback_pagination(self) -> None:
        """Pages contain only assessments for the requested feedback."""

        async def scenario(repository: AssessmentRepositoryABC) -> None:
            scope = PayloadScope(tenant_id="tenant", namespace="app", agent_id="agent")
            first = FeedbackAssessment(
                scope=scope,
                feedback_id=uuid4(),
                feedback_digest="a" * 64,
                experience_id=uuid4(),
                experience_version=1,
                experience_digest="b" * 64,
                status=AssessmentStatus.REVIEW_REQUIRED,
                method="contract",
                reviewer="reviewer",
            )
            second = first.model_copy(update={"assessment_id": uuid4()})
            unrelated = first.model_copy(update={"assessment_id": uuid4(), "feedback_id": uuid4()})
            for item in (first, unrelated, second):
                await repository.create(item)
            page = await repository.for_feedback(scope, first.feedback_id, limit=1)
            _check(page.assessments == (first,), "first assessment page changed")
            _check(page.next_cursor is not None, "first assessment page has no cursor")
            page = await repository.for_feedback(
                scope, first.feedback_id, limit=1, cursor=page.next_cursor
            )
            _check(page.assessments == (second,), "second assessment page changed")
            _check(page.next_cursor is None, "final assessment page has a cursor")
            _check(
                not (
                    await repository.for_feedback(
                        scope, first.feedback_id, cursor=second.assessment_id
                    )
                ).assessments,
                "cursor after final assessment must produce an empty page",
            )
            await _raises(
                LearningValidationError,
                repository.for_feedback(scope, first.feedback_id, cursor=unrelated.assessment_id),
                "cursor must belong to the requested feedback",
            )
            await _raises(
                LearningValidationError,
                repository.for_feedback(scope, first.feedback_id, cursor="invalid"),  # type: ignore[arg-type]
                "assessment cursor must be a UUID",
            )

        self._run(scenario)

    def test_decision_feed_and_experience_deletion(self) -> None:
        """Decisions form an ordered feed; deleting an experience is scoped and complete."""

        async def scenario(repository: AssessmentRepositoryABC) -> None:
            scope = PayloadScope(tenant_id="tenant", namespace="app", agent_id="agent")
            other = PayloadScope(tenant_id="other", namespace="app", agent_id="agent")
            experience_id = uuid4()

            def assessment(target: PayloadScope, experience: UUID) -> FeedbackAssessment:
                return FeedbackAssessment(
                    scope=target,
                    feedback_id=uuid4(),
                    feedback_digest="a" * 64,
                    experience_id=experience,
                    experience_version=1,
                    experience_digest="b" * 64,
                    status=AssessmentStatus.SUPPORTED,
                    method="contract",
                    reviewer="critic",
                    assessed_at=_EPOCH,
                    supporting_evidence=(
                        AssessmentEvidence(
                            evidence_id="independent",
                            scope=target,
                            experience_id=uuid4(),
                            experience_version=1,
                        ),
                    ),
                )

            items = (
                assessment(scope, experience_id),
                assessment(scope, uuid4()),
                assessment(other, experience_id),
            )
            decisions = []
            for index, item in enumerate(items):
                await repository.create(item)
                decisions.append(
                    await repository.decide(
                        LearningDecision(
                            scope=item.scope,
                            assessment_id=item.assessment_id,
                            assessment_version=1,
                            accepted=index != 1,
                            reviewer="host-reviewer",
                            decided_at=_EPOCH + timedelta(seconds=1),
                        )
                    )
                )
            first = await repository.decision_changes(limit=2)
            _check(
                [(change.feedback_id, change.decision) for change in first.changes]
                == [
                    (item.feedback_id, decision)
                    for item, decision in zip(items[:2], decisions[:2], strict=True)
                ],
                "first decision feed page changed",
            )
            _check(first.has_more and first.cursor is not None, "first feed page is incomplete")
            second = await repository.decision_changes(after=first.cursor, limit=2)
            _check(
                [change.decision for change in second.changes] == decisions[2:],
                "second decision feed page changed",
            )
            _check(not second.has_more, "final feed page has more")
            end = await repository.decision_changes(after=second.cursor)
            _check(
                not end.changes and end.cursor == second.cursor and not end.has_more,
                "empty feed page must keep its cursor",
            )
            await _raises(
                LearningValidationError,
                repository.decision_changes(after="not-a-cursor"),
                "invalid feed cursors must fail",
            )
            page = await repository.for_experience(scope, experience_id, limit=1)
            _check(
                page.assessments == (items[0],) and page.next_cursor is None,
                "experience assessment page changed",
            )
            await _raises(
                LearningValidationError,
                repository.for_experience(scope, experience_id, cursor=items[1].assessment_id),
                "cursor must belong to the requested experience",
            )
            deleted = await repository.delete_for_experience(scope, experience_id)
            _check(
                deleted.assessments == (items[0],) and deleted.decisions == (decisions[0],),
                "deletion did not report the experience's records",
            )
            await _raises(
                AssessmentNotFoundError,
                repository.get(scope, items[0].assessment_id),
                "deleted assessments must not be readable",
            )
            _check(
                [change.decision for change in (await repository.decision_changes()).changes]
                == decisions[1:],
                "deleted decisions must leave the feed",
            )
            _check(
                await repository.get(other, items[2].assessment_id) == items[2],
                "deletion crossed scopes",
            )
            _check(
                not (await repository.for_experience(scope, experience_id)).assessments,
                "deleted assessments must leave experience pages",
            )
            repeated = await repository.delete_for_experience(scope, experience_id)
            _check(
                not repeated.assessments and not repeated.decisions,
                "repeated deletion must be a no-op",
            )

        self._run(scenario)


class ExperienceRepositoryContract(_Contract[ExperienceRepositoryABC]):
    """Tests the scoped, versioned experience archive contract."""

    @abstractmethod
    async def make_experience_repository(self) -> ExperienceRepositoryABC:
        """Return a new, empty, ready-to-use repository."""

    async def _make(self) -> ExperienceRepositoryABC:
        return await self.make_experience_repository()

    def test_create_update_and_version_history(self) -> None:
        """Create retries are idempotent and updates preserve prior versions."""

        async def scenario(repository: ExperienceRepositoryABC) -> None:
            scope = PayloadScope(tenant_id="tenant", namespace="app", agent_id="agent")
            experience = ExecutionExperience(
                scope=scope, execution_id="run", created_at=_EPOCH, updated_at=_EPOCH
            )
            _check(await repository.create(experience) == experience, "create changed experience")
            await _raises(
                ExperienceConflictError,
                repository.create(experience.model_copy(update={"status": "different"})),
                "conflicting create must fail",
            )
            await _raises(
                ExperienceConflictError,
                repository.create(
                    experience.model_copy(update={"experience_id": uuid4(), "version": 2})
                ),
                "new experiences must start at version one",
            )
            updated = experience.model_copy(
                update={
                    "version": 2,
                    "updated_at": _EPOCH + timedelta(seconds=1),
                    "status": "completed",
                }
            )
            _check(
                await repository.update(updated, expected_version=1) == updated,
                "update changed experience",
            )
            _check(
                await repository.create(experience) == updated,
                "identical create retry did not return the current version",
            )
            _check(
                await repository.get(scope, experience.experience_id, version=1) == experience,
                "the first version changed",
            )
            _check(
                await repository.get(scope, experience.experience_id) == updated,
                "get did not return the current version",
            )
            _check(
                tuple(await repository.versions(scope, experience.experience_id))
                == (experience, updated),
                "version history changed",
            )
            _check(
                tuple(await repository.versions(scope, experience.experience_id, limit=1))
                == (experience,),
                "first history page changed",
            )
            _check(
                tuple(
                    await repository.versions(
                        scope, experience.experience_id, after_version=1, limit=1
                    )
                )
                == (updated,),
                "second history page changed",
            )
            await _raises(
                ExperienceConflictError,
                repository.update(updated, expected_version=1),
                "stale updates must conflict",
            )
            await repository.health()

        self._run(scenario)

    def test_scoped_reads_and_pagination(self) -> None:
        """Different tenants, namespaces and agents are isolated."""

        async def scenario(repository: ExperienceRepositoryABC) -> None:
            scope = PayloadScope(tenant_id="tenant", namespace="app", agent_id="agent")
            first = await repository.create(ExecutionExperience(scope=scope, execution_id="one"))
            second = await repository.create(ExecutionExperience(scope=scope, execution_id="two"))
            page = await repository.query(scope, limit=1)
            _check(page.experiences == (first,), "first page changed")
            _check(page.next_cursor is not None, "first page has no cursor")
            next_page = await repository.query(scope, limit=1, cursor=page.next_cursor)
            _check(next_page.experiences == (second,), "second page changed")
            _check(next_page.next_cursor is None, "final page has a cursor")
            await _raises(
                LearningValidationError,
                repository.query(scope, cursor=uuid4()),
                "unknown cursors must fail",
            )
            await _raises(
                LearningValidationError,
                repository.query(scope, cursor="invalid"),  # type: ignore[arg-type]
                "non-UUID cursors must fail",
            )
            for other in (
                PayloadScope(tenant_id="other", namespace="app", agent_id="agent"),
                PayloadScope(tenant_id="tenant", namespace="other", agent_id="agent"),
                PayloadScope(tenant_id="tenant", namespace="app", agent_id="other"),
            ):
                await _raises(
                    ExperienceNotFoundError,
                    repository.get(other, first.experience_id),
                    "get must not cross experience scopes",
                )
                _check(not (await repository.query(other)).experiences, "query crossed scopes")
                await _raises(
                    ExperienceNotFoundError,
                    repository.versions(other, first.experience_id),
                    "version history must not cross experience scopes",
                )

        self._run(scenario)

    def test_delete_removes_every_version_within_scope(self) -> None:
        """Deletion removes history, is idempotent, and cannot cross scopes."""

        async def scenario(repository: ExperienceRepositoryABC) -> None:
            scope = PayloadScope(tenant_id="tenant", namespace="app", agent_id="agent")
            experience = ExecutionExperience(
                scope=scope, execution_id="run", created_at=_EPOCH, updated_at=_EPOCH
            )
            await repository.create(experience)
            await repository.update(
                experience.model_copy(
                    update={"version": 2, "updated_at": _EPOCH + timedelta(seconds=1)}
                ),
                expected_version=1,
            )
            kept = await repository.create(ExecutionExperience(scope=scope, execution_id="kept"))
            other = PayloadScope(tenant_id="other", namespace="app", agent_id="agent")
            _check(
                not await repository.delete(other, experience.experience_id),
                "delete crossed experience scopes",
            )
            _check(await repository.delete(scope, experience.experience_id), "delete missed")
            _check(
                not await repository.delete(scope, experience.experience_id),
                "repeated delete must report a missing experience",
            )
            for version in (None, 1):
                await _raises(
                    ExperienceNotFoundError,
                    repository.get(scope, experience.experience_id, version=version),
                    "deleted experience versions must not be readable",
                )
            _check(
                (await repository.query(scope)).experiences == (kept,),
                "delete removed another experience",
            )
            recreated = await repository.create(experience)
            _check(recreated == experience, "a deleted experience ID could not be reused")

        self._run(scenario)


def _vector(
    record_id: str,
    vector: tuple[float, ...] = (1.0, 0.0),
    *,
    scope: TenantScope = _SCOPE,
    space: EmbeddingSpace = _SPACE,
    metadata: dict[str, Any] | None = None,
) -> VectorRecord:
    return VectorRecord(
        record_id=record_id,
        scope=scope,
        artifact_id=UUID(int=1),
        artifact_version=1,
        space=space,
        vector=vector,
        metadata=metadata or {},
    )


def _record_ids(items: Iterable[Any]) -> list[str]:
    return [item.record.record_id for item in items]


class VectorStoreContract(_Contract[VectorStore]):
    """Tests every promise a `VectorStore` makes to the projector and retriever."""

    @abstractmethod
    async def make_vector_store(self) -> VectorStore:
        """Return a new, empty, ready-to-use vector store."""

    async def _make(self) -> VectorStore:
        return await self.make_vector_store()

    def test_capabilities_are_declared(self) -> None:
        """``capabilities`` returns a `VectorCapabilities`."""

        async def scenario(store: VectorStore) -> None:
            capabilities = store.capabilities
            _check(
                isinstance(capabilities, VectorCapabilities),
                f"capabilities returned {capabilities!r}",
            )
            await store.health()

        self._run(scenario)

    def test_upsert_is_idempotent_and_latest_wins(self) -> None:
        """Rewriting a record replaces it, and the last duplicate in a batch wins."""

        async def scenario(store: VectorStore) -> None:
            first = _vector("one", metadata={"n": 1})
            await store.upsert(first)
            await store.upsert(first)
            second = _vector("one", metadata={"n": 2})
            third = _vector("one", metadata={"n": 3})
            await store.upsert_many([second, third])
            found = tuple(await store.get(_SCOPE, _SPACE, ["one"]))
            _check(found == (third,), f"expected only the latest record, got {found!r}")

        self._run(scenario)

    def test_get_returns_present_records_in_request_order(self) -> None:
        """``get`` skips missing IDs and keeps the requested order."""

        async def scenario(store: VectorStore) -> None:
            records = [_vector(name) for name in ("a", "b", "c")]
            await store.upsert_many(records)
            found = tuple(await store.get(_SCOPE, _SPACE, ["c", "missing", "a"]))
            _check(found == (records[2], records[0]), f"get returned {found!r}")
            _check(tuple(await store.get(_SCOPE, _SPACE, [])) == (), "an empty get must be empty")

        self._run(scenario)

    def test_scopes_and_spaces_are_isolated(self) -> None:
        """Equal IDs in another tenant, namespace, or embedding space are separate records."""

        async def scenario(store: VectorStore) -> None:
            mine = _vector("shared", metadata={"owner": "mine"})
            others = [
                _vector("shared", scope=_OTHER_TENANT),
                _vector("shared", scope=_OTHER_NAMESPACE),
                _vector("shared", space=_OTHER_SPACE),
            ]
            await store.upsert_many([mine, *others])
            matches = await store.search(_SCOPE, _SPACE, (1.0, 0.0), limit=10)
            _check([match.record for match in matches] == [mine], f"search leaked {matches!r}")
            await store.delete(_SCOPE, _SPACE, ["shared"])
            _check(tuple(await store.get(_SCOPE, _SPACE, ["shared"])) == (), "delete failed")
            for other in others:
                found = tuple(await store.get(other.scope, other.space, ["shared"]))
                _check(found == (other,), f"deleting one scope removed {other!r}")

        self._run(scenario)

    def test_search_ranks_by_cosine_distance_with_ties_by_id(self) -> None:
        """Results are ordered by cosine distance, then by record ID, up to ``limit``."""

        async def scenario(store: VectorStore) -> None:
            await store.upsert_many(
                [
                    _vector("opposite", (-1.0, 0.0)),
                    _vector("orthogonal", (0.0, 2.0)),
                    _vector("b-exact", (4.0, 0.0)),
                    _vector("a-exact", (1.0, 0.0)),
                ]
            )
            matches = await store.search(_SCOPE, _SPACE, (1.0, 0.0), limit=3)
            found = _record_ids(matches)
            _check(found == ["a-exact", "b-exact", "orthogonal"], f"search returned {found!r}")
            distances = [match.distance for match in matches]
            _check(
                all(
                    math.isclose(a, b, abs_tol=1e-6)
                    for a, b in zip(distances, [0, 0, 1], strict=True)
                ),
                f"distances were {distances!r}",
            )

        self._run(scenario)

    def test_metadata_filters_match_every_pair(self) -> None:
        """A metadata filter keeps only records containing every requested pair."""

        async def scenario(store: VectorStore) -> None:
            await store.upsert_many(
                [
                    _vector("close", (1.0, 0.0), metadata={"kind": "rule", "tool": "x"}),
                    _vector("wrong", (1.0, 0.1), metadata={"kind": "rule", "tool": "y"}),
                    _vector("missing", (1.0, 0.2), metadata={"kind": "rule"}),
                    _vector("far", (0.0, 1.0), metadata={"kind": "rule", "tool": "x", "z": 1}),
                ]
            )
            matches = await store.search(
                _SCOPE, _SPACE, (1.0, 0.0), limit=5, metadata={"kind": "rule", "tool": "x"}
            )
            _check(_record_ids(matches) == ["close", "far"], f"filter returned {matches!r}")

        self._run(scenario)

    def test_invalid_dimensions_are_rejected(self) -> None:
        """A query vector with the wrong dimensions raises `ValueError`."""

        async def scenario(store: VectorStore) -> None:
            await store.upsert(_vector("one"))
            await _raises(
                ValueError,
                store.search(_SCOPE, _SPACE, (1.0, 0.0, 0.0), limit=1),
                "a query with the wrong dimensions must raise ValueError",
            )

        self._run(scenario)

    def test_delete_is_idempotent_and_identifiers_are_data(self) -> None:
        """Deletes ignore missing IDs, and hostile identifiers never match other records."""

        async def scenario(store: VectorStore) -> None:
            hostile_scope = TenantScope(tenant_id=_HOSTILE, namespace="ns'--")
            hostile = _vector(_HOSTILE, scope=hostile_scope)
            victim = _vector("victim")
            await store.upsert_many([hostile, victim])
            await store.delete(_SCOPE, _SPACE, [_HOSTILE, "x' OR record_id != '", "missing"])
            await store.delete(_SCOPE, _SPACE, [])
            found = tuple(await store.get(_SCOPE, _SPACE, ["victim"]))
            _check(found == (victim,), "a hostile delete removed another record")
            found = tuple(await store.get(hostile_scope, _SPACE, [_HOSTILE]))
            _check(found == (hostile,), "a hostile identifier was not stored literally")

        self._run(scenario)


def _node(node_id: str, *, scope: TenantScope = _SCOPE, **properties: Any) -> GraphNode:
    return GraphNode(node_id=node_id, scope=scope, node_type="Artifact", properties=properties)


def _edge(
    relationship_id: str,
    source: str,
    target: str,
    *,
    scope: TenantScope = _SCOPE,
    relationship_type: str = "KNOWS",
) -> GraphRelationship:
    return GraphRelationship(
        relationship_id=relationship_id,
        scope=scope,
        source_id=source,
        target_id=target,
        relationship_type=relationship_type,
    )


def _graph_ids(items: Iterable[GraphNode | GraphRelationship]) -> list[str]:
    return [item.node_id if isinstance(item, GraphNode) else item.relationship_id for item in items]


async def _seed_graph(store: GraphStore, scope: TenantScope = _SCOPE) -> None:
    """Build a -KNOWS-> b -KNOWS-> c, a -LIKES-> d, and e -KNOWS-> a."""
    await store.upsert_nodes([_node(node_id, scope=scope) for node_id in "abcde"])
    await store.upsert_relationships(
        [
            _edge("ab", "a", "b", scope=scope),
            _edge("bc", "b", "c", scope=scope),
            _edge("ad", "a", "d", scope=scope, relationship_type="LIKES"),
            _edge("ea", "e", "a", scope=scope),
        ]
    )


class GraphStoreContract(_Contract[GraphStore]):
    """Tests every promise a `GraphStore` makes to the projector and retriever."""

    @abstractmethod
    async def make_graph_store(self) -> GraphStore:
        """Return a new, empty, ready-to-use graph store."""

    async def _make(self) -> GraphStore:
        return await self.make_graph_store()

    def test_capabilities_are_declared(self) -> None:
        """``capabilities`` returns a `GraphCapabilities`."""

        async def scenario(store: GraphStore) -> None:
            capabilities = store.capabilities
            _check(
                isinstance(capabilities, GraphCapabilities),
                f"capabilities returned {capabilities!r}",
            )
            await store.health()

        self._run(scenario)

    def test_node_upsert_is_idempotent_and_updates(self) -> None:
        """Rewriting a node replaces it; ``get_nodes`` skips missing IDs."""

        async def scenario(store: GraphStore) -> None:
            first = _node("one", weight=1)
            await store.upsert_node(first)
            await store.upsert_node(first)
            updated = _node("one", weight=2)
            await store.upsert_nodes([updated, _node("two")])
            found = list(await store.get_nodes(_SCOPE, ["two", "missing", "one"]))
            _check(found == [_node("two"), updated], f"get_nodes returned {found!r}")
            await store.upsert_nodes([])
            await store.upsert_relationships([])
            await store.delete_nodes(_SCOPE, [])
            await store.delete_relationships(_SCOPE, [])

        self._run(scenario)

    def test_relationship_endpoints_must_exist_in_scope(self) -> None:
        """A relationship to a missing or out-of-scope node is rejected."""

        async def scenario(store: GraphStore) -> None:
            await store.upsert_nodes([_node("a"), _node("b")])
            await store.upsert_node(_node("elsewhere", scope=_OTHER_TENANT))
            await _raises(
                ValueError,
                store.upsert_relationship(_edge("r", "a", "missing")),
                "a relationship to a missing node must raise a validation error",
            )
            await _raises(
                ValueError,
                store.upsert_relationship(_edge("x", "a", "elsewhere")),
                "a relationship across scopes must raise a validation error",
            )
            page = await store.traverse(_SCOPE, ["a"], direction=GraphDirection.BOTH)
            _check(page.relationships == (), f"a rejected relationship was stored: {page!r}")

        self._run(scenario)

    def test_traversal_respects_direction_types_and_depth(self) -> None:
        """Traversal follows the requested direction, types, and depth."""

        async def scenario(store: GraphStore) -> None:
            await _seed_graph(store)
            outgoing = await store.traverse(_SCOPE, ["a"], max_depth=2)
            _check(
                sorted(_graph_ids(outgoing.relationships)) == ["ab", "ad", "bc"],
                f"outgoing traversal returned {outgoing!r}",
            )
            incoming = await store.traverse(_SCOPE, ["a"], direction=GraphDirection.INCOMING)
            _check(
                _graph_ids(incoming.relationships) == ["ea"],
                f"incoming traversal returned {incoming!r}",
            )
            likes = await store.traverse(_SCOPE, ["a"], relationship_types=["LIKES"])
            _check(_graph_ids(likes.relationships) == ["ad"], f"type filter returned {likes!r}")
            shallow = await store.traverse(_SCOPE, ["a"], max_depth=1)
            _check("bc" not in _graph_ids(shallow.relationships), "max_depth=1 went too deep")

        self._run(scenario)

    def test_every_operation_is_scope_isolated(self) -> None:
        """Equal IDs in another tenant or namespace are a separate graph."""

        async def scenario(store: GraphStore) -> None:
            await _seed_graph(store)
            for other in (_OTHER_TENANT, _OTHER_NAMESPACE):
                await _seed_graph(store, other)
                await store.delete_relationships(other, ["ab"])
                await store.delete_nodes(other, ["c"])
            nodes = _graph_ids(await store.get_nodes(_SCOPE, list("abcde")))
            _check(nodes == list("abcde"), f"another scope's delete removed nodes: {nodes!r}")
            page = await store.traverse(_SCOPE, ["a"], max_depth=3)
            _check(
                sorted(_graph_ids(page.relationships)) == ["ab", "ad", "bc"],
                f"another scope's delete removed relationships: {page!r}",
            )
            _check(
                all(item.scope == _SCOPE for item in (*page.nodes, *page.relationships)),
                "traversal returned items from another scope",
            )

        self._run(scenario)

    def test_deleting_nodes_cascades_and_is_idempotent(self) -> None:
        """Deleting a node removes its relationships; repeated deletes are no-ops."""

        async def scenario(store: GraphStore) -> None:
            await _seed_graph(store)
            await store.delete_nodes(_SCOPE, ["b", "missing"])
            await store.delete_nodes(_SCOPE, ["b"])
            await store.delete_relationships(_SCOPE, ["ad", "missing"])
            await store.delete_relationships(_SCOPE, ["ad"])
            page = await store.traverse(_SCOPE, ["a"], direction=GraphDirection.BOTH)
            _check(_graph_ids(page.relationships) == ["ea"], f"traversal returned {page!r}")

        self._run(scenario)

    def test_traversal_pages_with_a_cursor(self) -> None:
        """Following ``next_cursor`` returns every reachable node exactly once."""

        async def scenario(store: GraphStore) -> None:
            await _seed_graph(store)
            if not store.capabilities.pagination:
                return
            seen: list[str] = []
            cursor: str | None = None
            while True:
                page = await store.traverse(
                    _SCOPE,
                    ["a"],
                    direction=GraphDirection.BOTH,
                    max_depth=3,
                    limit=2,
                    cursor=cursor,
                )
                _check(len(page.nodes) <= 2, f"a page exceeded its limit: {page!r}")
                seen.extend(_graph_ids(page.nodes))
                cursor = page.next_cursor
                if cursor is None:
                    break
            _check(sorted(seen) == list("abcde"), f"pagination returned {seen!r}")

        self._run(scenario)


def _job(index: int) -> LearningJob:
    scope = ArtifactScope(tenant_id="contract-tenant", agent_id="agent")
    created = _EPOCH + timedelta(seconds=index)
    return LearningJob.for_feedback(UUID(int=index + 1), FeedbackStatus.RESOLVED, scope).model_copy(
        update={"available_at": created, "created_at": created, "updated_at": created}
    )


class LearningJobStoreContract(_Contract[LearningJobStore]):
    """Tests every promise a `LearningJobStore` makes to `BackgroundLearner`."""

    @abstractmethod
    async def make_job_store(self) -> LearningJobStore:
        """Return a new, empty, ready-to-use job store."""

    async def _make(self) -> LearningJobStore:
        return await self.make_job_store()

    def test_enqueue_deduplicates_by_job_id(self) -> None:
        """Enqueueing a job twice stores it once."""

        async def scenario(store: LearningJobStore) -> None:
            job = _job(0)
            _check(await store.enqueue(job), "the first enqueue must return True")
            _check(not await store.enqueue(job), "a duplicate enqueue must return False")
            fetched = await store.get(job.job_id)
            _check(fetched == job, f"get returned {fetched!r}")
            await _raises(
                LearningJobNotFoundError,
                store.get(uuid4()),
                "get must raise LearningJobNotFoundError for an unknown job",
            )

        self._run(scenario)

    def test_claim_leases_due_jobs_oldest_first(self) -> None:
        """Claims lease due jobs once, oldest first, and reclaim expired leases."""

        async def scenario(store: LearningJobStore) -> None:
            jobs = [_job(index) for index in (2, 0, 1)]
            for job in jobs:
                await store.enqueue(job)
            later = _job(9).model_copy(update={"available_at": _EPOCH + timedelta(days=1)})
            await store.enqueue(later)
            now = _EPOCH + timedelta(hours=1)
            claimed = await store.claim(limit=2, lease=timedelta(minutes=1), now=now)
            expected = [jobs[1].job_id, jobs[2].job_id]
            _check([job.job_id for job in claimed] == expected, f"claimed {claimed!r}")
            _check(
                all(
                    job.status is LearningJobStatus.RUNNING and job.attempts == 1 for job in claimed
                ),
                "claimed jobs must be running with one attempt",
            )
            rest = await store.claim(limit=10, lease=timedelta(minutes=1), now=now)
            _check([job.job_id for job in rest] == [jobs[0].job_id], f"second claim got {rest!r}")
            expired = await store.claim(
                limit=10, lease=timedelta(minutes=1), now=now + timedelta(minutes=2)
            )
            _check(len(expired) == 3, f"expired leases were not reclaimed: {expired!r}")
            _check(all(job.attempts == 2 for job in expired), "reclaiming must bump attempts")

        self._run(scenario)

    def test_stale_workers_lose_their_lease(self) -> None:
        """Completing with an old attempt raises `LearningLeaseLostError`."""

        async def scenario(store: LearningJobStore) -> None:
            job = _job(0)
            await store.enqueue(job)
            now = _EPOCH + timedelta(hours=1)
            (first,) = await store.claim(limit=1, lease=timedelta(seconds=1), now=now)
            (second,) = await store.claim(
                limit=1, lease=timedelta(minutes=1), now=now + timedelta(seconds=2)
            )
            await _raises(
                LearningLeaseLostError,
                store.complete(job.job_id, attempt=first.attempts),
                "a stale attempt must raise LearningLeaseLostError",
            )
            done = await store.complete(job.job_id, attempt=second.attempts)
            _check(done.status is LearningJobStatus.SUCCEEDED, f"complete returned {done!r}")

        self._run(scenario)

    def test_retry_dead_letter_and_requeue(self) -> None:
        """Retries reschedule, dead letters park, and requeue makes a job pending again."""

        async def scenario(store: LearningJobStore) -> None:
            job = _job(0)
            await store.enqueue(job)
            now = _EPOCH + timedelta(hours=1)
            (claimed,) = await store.claim(limit=1, lease=timedelta(minutes=1), now=now)
            retry_at = now + timedelta(minutes=5)
            retried = await store.retry(
                job.job_id, attempt=claimed.attempts, error="boom", available_at=retry_at
            )
            _check(
                retried.status is LearningJobStatus.PENDING and retried.last_error == "boom",
                f"retry returned {retried!r}",
            )
            early = await store.claim(limit=1, lease=timedelta(minutes=1), now=now)
            _check(early == (), "a retried job was claimed before it was due")
            (claimed,) = await store.claim(limit=1, lease=timedelta(minutes=1), now=retry_at)
            dead = await store.dead_letter(job.job_id, attempt=claimed.attempts, error="fatal")
            _check(dead.status is LearningJobStatus.DEAD_LETTERED, f"dead_letter returned {dead!r}")
            counts = await store.counts()
            _check(counts[LearningJobStatus.DEAD_LETTERED] == 1, f"counts were {counts!r}")
            listed = await store.list(status=LearningJobStatus.DEAD_LETTERED)
            _check([item.job_id for item in listed] == [job.job_id], f"list returned {listed!r}")
            requeued = await store.requeue(job.job_id)
            _check(requeued.status is LearningJobStatus.PENDING, f"requeue returned {requeued!r}")

        self._run(scenario)

    def test_checkpoints_round_trip_per_source(self) -> None:
        """Checkpoints are stored per source and start empty."""

        async def scenario(store: LearningJobStore) -> None:
            _check(await store.checkpoint("feed") is None, "a new checkpoint must be None")
            await store.save_checkpoint("feed", "cursor-1")
            await store.save_checkpoint("feed", "cursor-2")
            await store.save_checkpoint("other", "cursor-x")
            _check(await store.checkpoint("feed") == "cursor-2", "the latest checkpoint was lost")
            _check(await store.checkpoint("other") == "cursor-x", "checkpoints were shared")

        self._run(scenario)


class EmbeddingProviderContract(_Contract[EmbeddingProvider]):
    """Tests every promise an `EmbeddingProvider` makes to the projector and retriever."""

    documents: tuple[str, ...] = ("Validate before execution.", "Refunds need an order total.")
    """The documents embedded by the tests; override them for a domain-specific provider."""

    @abstractmethod
    async def make_provider(self) -> EmbeddingProvider:
        """Return a ready-to-use provider."""

    async def _make(self) -> EmbeddingProvider:
        return await self.make_provider()

    def _check_vector(self, space: EmbeddingSpace, vector: Iterable[float], label: str) -> None:
        values = list(vector)
        _check(len(values) == space.dimensions, f"{label} has {len(values)} dimension(s)")
        _check(all(math.isfinite(value) for value in values), f"{label} is not finite")
        if space.normalized:
            norm = math.sqrt(sum(value * value for value in values))
            _check(math.isclose(norm, 1.0, abs_tol=1e-6) or norm == 0, f"{label} is not unit")

    def test_space_is_stable(self) -> None:
        """``space`` is an `EmbeddingSpace` and does not change between reads."""

        async def scenario(provider: EmbeddingProvider) -> None:
            space = provider.space
            _check(isinstance(space, EmbeddingSpace), f"space returned {space!r}")
            _check(provider.space == space, "space changed between reads")
            await provider.health()

        self._run(scenario)

    def test_documents_embed_one_vector_each(self) -> None:
        """``embed_documents`` returns one valid vector per document, in order."""

        async def scenario(provider: EmbeddingProvider) -> None:
            vectors = list(await provider.embed_documents(self.documents))
            _check(len(vectors) == len(self.documents), f"got {len(vectors)} vector(s)")
            for index, vector in enumerate(vectors):
                self._check_vector(provider.space, vector, f"document vector {index}")
            empty = list(await provider.embed_documents(()))
            _check(empty == [], f"embedding no documents returned {empty!r}")

        self._run(scenario)

    def test_queries_share_the_document_space(self) -> None:
        """``embed_query`` returns one valid vector in the provider's space."""

        async def scenario(provider: EmbeddingProvider) -> None:
            vector = await provider.embed_query(self.documents[0])
            self._check_vector(provider.space, vector, "query vector")

        self._run(scenario)
