"""Entry points run in spawned processes by the multi-process tests.

Each function opens its own stores, so the processes share only the files.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from examples.sqlite_artifact_repository import SQLiteArtifactRepository
from feedback_manager.learning.models import (
    ArtifactScope,
    ArtifactStatus,
    ArtifactType,
    Evidence,
    LearnedArtifact,
    TenantScope,
    ValidationRecord,
)
from feedback_manager.retrieval import RetrievalLoomRetriever

SCOPE = ArtifactScope(tenant_id="tenant", agent_id="agent")
TENANT = TenantScope(tenant_id="tenant")


def _next(artifact: LearnedArtifact, **changes: object) -> LearnedArtifact:
    return artifact.model_copy(
        update={
            "version": artifact.version + 1,
            "updated_at": artifact.updated_at + timedelta(microseconds=1),
            **changes,
        }
    )


async def _activate(repository: SQLiteArtifactRepository, content: str) -> LearnedArtifact:
    evidence = tuple(
        Evidence(execution_id=f"run-{index}", source="human", independence_key=f"run-{index}")
        for index in range(2)
    )
    candidate = await repository.create(
        LearnedArtifact(
            artifact_type=ArtifactType.STRATEGY,
            title="Reconcile",
            content=content,
            scope=SCOPE,
            evidence=evidence,
            confidence=0.9,
        )
    )
    review = await repository.update_with_projection(
        _next(candidate, status=ArtifactStatus.UNDER_REVIEW), expected_version=1
    )
    record = ValidationRecord(
        reviewer="reviewer",
        approved=True,
        policy_version="default-v1",
        reviewed_version=review.version,
        artifact_digest=review.review_digest(),
    )
    validated = await repository.update_with_projection(
        _next(review, status=ArtifactStatus.VALIDATED, validation_history=(record,)),
        expected_version=2,
    )
    return await repository.update_with_projection(
        _next(validated, status=ArtifactStatus.ACTIVE), expected_version=3
    )


async def _publish(path: Path, contents: list[str]) -> list[str]:
    repository = SQLiteArtifactRepository(path)
    retriever = RetrievalLoomRetriever(repository)
    published = []
    try:
        for content in contents:
            artifact = await _activate(repository, content)
            published.append(str(artifact.artifact_id))
            # Each process projects into its own engine and acknowledges the shared outbox.
            await retriever.drain_intents(TENANT)
    finally:
        await retriever.aclose()
        await repository.close()
    return published


def publish(path: str, contents: list[str]) -> list[str]:
    """Activate artifacts with outbox intents, draining after each one."""
    return asyncio.run(_publish(Path(path), contents))


async def _deprecate(path: Path, artifact_id: UUID) -> None:
    repository = SQLiteArtifactRepository(path)
    retriever = RetrievalLoomRetriever(repository)
    try:
        current = await repository.get(TENANT, artifact_id)
        await repository.update_with_projection(
            _next(current, status=ArtifactStatus.DEPRECATED), expected_version=current.version
        )
        await retriever.drain_intents(TENANT)
    finally:
        await retriever.aclose()
        await repository.close()


def deprecate(path: str, artifact_id: str) -> None:
    """Deprecate an artifact and drain the outbox from this process."""
    asyncio.run(_deprecate(Path(path), UUID(artifact_id)))
