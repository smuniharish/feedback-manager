import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest

from feedback_manager.learning import (
    ArtifactConflictError,
    ArtifactNotFoundError,
    ArtifactScope,
    ArtifactStatus,
    ArtifactType,
    Evidence,
    LearnedArtifact,
    LearningValidationError,
    TenantScope,
    ValidationRecord,
)
from feedback_manager.storage.artifacts import (
    ArtifactQuery,
    ArtifactRepository,
    InMemoryArtifactRepository,
)


@pytest.fixture(params=["memory", "sqlite"])
async def repository(request, tmp_path):
    """Reusable repository contract: replace this fixture to certify another provider."""
    if request.param == "memory":
        yield InMemoryArtifactRepository()
    else:
        from examples.sqlite_artifact_repository import SQLiteArtifactRepository

        provider = SQLiteArtifactRepository(tmp_path / "artifacts.db")
        try:
            yield provider
        finally:
            await provider.close()


def candidate(*, tenant: str = "tenant", namespace: str = "default") -> LearnedArtifact:
    return LearnedArtifact(
        artifact_type=ArtifactType.STRATEGY,
        title="Validate before execution",
        content="Search, validate, then execute.",
        scope=ArtifactScope(tenant_id=tenant, namespace=namespace, agent_id="agent"),
        provenance={"context": {"value": "original"}},
    )


def evolve(artifact: LearnedArtifact, **changes: object) -> LearnedArtifact:
    return artifact.model_copy(
        update={
            "version": artifact.version + 1,
            "updated_at": artifact.updated_at + timedelta(microseconds=1),
            **changes,
        }
    )


async def test_create_get_and_versions_are_scoped_and_copied(
    repository: ArtifactRepository,
) -> None:
    artifact = candidate()
    scope = TenantScope(tenant_id="tenant")
    stored = await repository.create(artifact)
    artifact.provenance["new"] = "changed"
    stored.provenance["new"] = "changed"
    assert (await repository.get(scope, artifact.artifact_id)).provenance == {
        "context": {"value": "original"}
    }

    original = await repository.get(scope, artifact.artifact_id)
    updated = evolve(original, status=ArtifactStatus.UNDER_REVIEW)
    assert (await repository.update(updated, expected_version=1)).version == 2
    assert (await repository.create(original)).version == 2
    assert [item.version for item in await repository.versions(scope, artifact.artifact_id)] == [
        1,
        2,
    ]
    assert (await repository.get(scope, artifact.artifact_id, version=1)).status == (
        ArtifactStatus.CANDIDATE
    )
    assert [item.version for item in await repository.get_many(scope, [artifact.artifact_id])] == [
        2
    ]
    await repository.health()

    with pytest.raises(ArtifactNotFoundError):
        await repository.get(TenantScope(tenant_id="other"), artifact.artifact_id)
    with pytest.raises(ArtifactNotFoundError):
        await repository.get(
            TenantScope(tenant_id="tenant", namespace="other"), artifact.artifact_id
        )
    with pytest.raises(ArtifactNotFoundError):
        await repository.get(scope, artifact.artifact_id, version=3)
    with pytest.raises(ArtifactNotFoundError):
        await repository.get_many(scope, [artifact.artifact_id, uuid4()])
    with pytest.raises(ArtifactNotFoundError):
        await repository.versions(scope, uuid4())


async def test_creation_conflicts_and_invalid_versions(repository: ArtifactRepository) -> None:
    artifact = candidate()
    await repository.create(artifact)
    with pytest.raises(ArtifactConflictError, match="already exists"):
        await repository.create(artifact.model_copy(update={"content": "different"}))
    with pytest.raises(ArtifactConflictError, match="version 1 candidates"):
        await repository.create(artifact.model_copy(update={"version": 2}))
    with pytest.raises(ArtifactConflictError, match="version 1 candidates"):
        await repository.create(artifact.model_copy(update={"status": ArtifactStatus.ACTIVE}))
    with pytest.raises(LearningValidationError, match="expected_version"):
        await repository.update(evolve(artifact), expected_version=0)
    with pytest.raises(LearningValidationError, match="version"):
        await repository.get(TenantScope(tenant_id="tenant"), artifact.artifact_id, version=0)
    with pytest.raises(ArtifactNotFoundError):
        await repository.update(evolve(candidate()), expected_version=1)


async def test_lifecycle_requires_review_and_preserves_history(
    repository: ArtifactRepository,
) -> None:
    artifact = await repository.create(candidate())
    with pytest.raises(ArtifactConflictError, match="illegal artifact lifecycle"):
        await repository.update(evolve(artifact, status=ArtifactStatus.ACTIVE), expected_version=1)

    review = await repository.update(
        evolve(artifact, status=ArtifactStatus.UNDER_REVIEW), expected_version=1
    )
    with pytest.raises(ArtifactConflictError, match="approved review"):
        await repository.update(evolve(review, status=ArtifactStatus.VALIDATED), expected_version=2)

    decision = ValidationRecord(
        reviewer="reviewer",
        approved=True,
        policy_version="1",
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
    with pytest.raises(ArtifactConflictError, match="new review"):
        await repository.update(evolve(active, content="modified"), expected_version=4)
    with pytest.raises(ArtifactConflictError, match="cannot be rewritten"):
        await repository.update(evolve(active, validation_history=()), expected_version=4)
    revised = await repository.update(
        evolve(active, status=ArtifactStatus.UNDER_REVIEW, content="revised"), expected_version=4
    )
    assert revised.version == 5


async def test_review_digest_must_match_historical_artifact(repository: ArtifactRepository) -> None:
    artifact = await repository.create(candidate())
    review = await repository.update(
        evolve(artifact, status=ArtifactStatus.UNDER_REVIEW), expected_version=1
    )
    decision = ValidationRecord(
        reviewer="reviewer",
        approved=True,
        policy_version="1",
        reviewed_version=review.version,
        artifact_digest="0" * 64,
    )
    with pytest.raises(ArtifactConflictError, match="review does not match"):
        await repository.update(
            evolve(review, status=ArtifactStatus.VALIDATED, validation_history=(decision,)),
            expected_version=2,
        )


async def test_updates_check_version_timestamps_type_and_terminal_status(
    repository: ArtifactRepository,
) -> None:
    artifact = await repository.create(candidate())
    with pytest.raises(ArtifactConflictError, match="version changed"):
        await repository.update(evolve(artifact), expected_version=2)
    with pytest.raises(ArtifactConflictError, match="increase by one"):
        await repository.update(evolve(artifact, version=3), expected_version=1)
    with pytest.raises(ArtifactConflictError, match="timestamps"):
        await repository.update(
            evolve(artifact, updated_at=artifact.updated_at), expected_version=1
        )
    with pytest.raises(ArtifactConflictError, match="type cannot change"):
        await repository.update(
            evolve(artifact, artifact_type=ArtifactType.PATTERN), expected_version=1
        )
    rejected = await repository.update(
        evolve(artifact, status=ArtifactStatus.REJECTED), expected_version=1
    )
    with pytest.raises(ArtifactConflictError, match="terminal"):
        await repository.update(evolve(rejected), expected_version=2)


async def test_compare_and_set_is_safe_for_concurrent_updates(
    repository: ArtifactRepository,
) -> None:
    artifact = await repository.create(candidate())
    results = await asyncio.gather(
        repository.update(evolve(artifact, title="First"), expected_version=1),
        repository.update(evolve(artifact, title="Second"), expected_version=1),
        return_exceptions=True,
    )
    assert sum(isinstance(item, LearnedArtifact) for item in results) == 1
    assert sum(isinstance(item, ArtifactConflictError) for item in results) == 1


async def test_source_feedback_filter_is_applied_before_pagination(
    repository: ArtifactRepository,
) -> None:
    feedback_id = uuid4()
    for matches in (False, True, False, True):
        item = candidate()
        evidence = Evidence(
            feedback_id=feedback_id if matches else uuid4(),
            source="human",
            independence_key=str(uuid4()),
        )
        await repository.create(item.model_copy(update={"evidence": (evidence,)}))
    query = ArtifactQuery(
        scope=TenantScope(tenant_id="tenant"), source_feedback_id=feedback_id, limit=1
    )
    first = await repository.query(query)
    assert len(first.artifacts) == 1
    assert first.next_cursor is not None
    second = await repository.query(query.model_copy(update={"cursor": first.next_cursor}))
    assert len(second.artifacts) == 1
    assert second.next_cursor is None
    assert first.artifacts[0].artifact_id != second.artifacts[0].artifact_id


async def test_scoped_pagination_filters_and_cursor_validation(
    repository: ArtifactRepository,
) -> None:
    artifacts = [candidate() for _ in range(3)]
    for artifact in artifacts:
        await repository.create(artifact)
    await repository.create(candidate(tenant="other"))
    await repository.create(candidate(namespace="other"))

    scope = TenantScope(tenant_id="tenant")
    first = await repository.query(ArtifactQuery(scope=scope, limit=2))
    assert [item.artifact_id for item in first.artifacts] == [
        artifacts[0].artifact_id,
        artifacts[1].artifact_id,
    ]
    assert first.next_cursor is not None
    second = await repository.query(ArtifactQuery(scope=scope, limit=2, cursor=first.next_cursor))
    assert [item.artifact_id for item in second.artifacts] == [artifacts[2].artifact_id]
    assert second.next_cursor is None
    empty = await repository.query(
        ArtifactQuery(scope=scope, statuses=(ArtifactStatus.ACTIVE,), limit=2)
    )
    assert not empty.artifacts
    empty_type = await repository.query(
        ArtifactQuery(scope=scope, artifact_types=(ArtifactType.KNOWLEDGE,), limit=2)
    )
    assert not empty_type.artifacts
    with pytest.raises(LearningValidationError, match="invalid artifact cursor"):
        await repository.query(ArtifactQuery(scope=scope, cursor="bad"))
    with pytest.raises(LearningValidationError, match="cursor"):
        await repository.query(ArtifactQuery(scope=scope, cursor=str(uuid4())))
