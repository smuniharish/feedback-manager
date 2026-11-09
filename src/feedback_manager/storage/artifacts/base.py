"""The authoritative repository contract for versioned learned artifacts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from feedback_manager.core._types import Identifier
from feedback_manager.learning.errors import UnsupportedCapabilityError
from feedback_manager.learning.models import (
    ArtifactStatus,
    ArtifactType,
    LearnedArtifact,
    TenantScope,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


class ArtifactQuery(BaseModel):
    """Scoped filters and a bounded page for authoritative artifact reads."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: TenantScope
    statuses: tuple[ArtifactStatus, ...] = ()
    artifact_types: tuple[ArtifactType, ...] = ()
    source_feedback_id: UUID | None = None
    enforce_visibility: bool = False
    agent_id: Identifier | None = None
    agent_type: Identifier | None = None
    task_type: Identifier | None = None
    tool: Identifier | None = None
    workflow: Identifier | None = None
    limit: int = Field(default=50, ge=1, le=1000)
    cursor: Identifier | None = None


class ArtifactPage(BaseModel):
    """A page of authoritative artifact snapshots."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifacts: tuple[LearnedArtifact, ...]
    next_cursor: Identifier | None = None


class ProjectionIntent(BaseModel):
    """Durable request to refresh projections after an authoritative version commit."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    intent_id: UUID
    scope: TenantScope
    artifact_id: UUID
    artifact_version: int = Field(ge=1)

    @classmethod
    def for_artifact(cls, artifact: LearnedArtifact) -> ProjectionIntent:
        """Create a stable intent identity for one scoped artifact version."""
        key = f"{artifact.scope.tenant_id!r}:{artifact.scope.namespace!r}"
        key += f":{artifact.artifact_id}:{artifact.version}"
        return cls(
            intent_id=uuid5(NAMESPACE_URL, f"feedback-manager:projection:{key}"),
            scope=TenantScope(
                tenant_id=artifact.scope.tenant_id, namespace=artifact.scope.namespace
            ),
            artifact_id=artifact.artifact_id,
            artifact_version=artifact.version,
        )


class ArtifactRepository(ABC):
    """Store artifact snapshots and versions independently of projections.

    Every operation must enforce tenant and namespace isolation. Agent-level
    authorization is enforced by the host and the learning service before
    exposing repository results to an agent.
    """

    durable: bool = False
    """Whether committed versions survive a process restart."""
    atomic_projection_intents: bool = False
    """Whether a version and its projection intent can be committed atomically."""

    async def update_with_projection(
        self, artifact: LearnedArtifact, *, expected_version: int
    ) -> LearnedArtifact:
        """Atomically append a version and its projection intent, or fail explicitly."""
        raise UnsupportedCapabilityError(
            "atomic projection publication is unavailable",
            artifact_id=artifact.artifact_id,
            expected_version=expected_version,
        )

    async def pending_projections(
        self, scope: TenantScope, *, limit: int = 100
    ) -> Sequence[ProjectionIntent]:
        """Return committed intents in order for one serialized projector per scope."""
        raise UnsupportedCapabilityError(
            "atomic projection publication is unavailable", scope=scope, limit=limit
        )

    async def acknowledge_projection(self, scope: TenantScope, intent_id: UUID) -> None:
        """Remove an intent after all configured projections succeeded."""
        raise UnsupportedCapabilityError(
            "atomic projection publication is unavailable", scope=scope, intent_id=intent_id
        )

    @abstractmethod
    async def create(self, artifact: LearnedArtifact) -> LearnedArtifact:
        """Create an artifact, raising an error on conflicting IDs."""

    @abstractmethod
    async def update(self, artifact: LearnedArtifact, *, expected_version: int) -> LearnedArtifact:
        """Append a version only when the current version equals ``expected_version``."""

    @abstractmethod
    async def get(
        self, scope: TenantScope, artifact_id: UUID, *, version: int | None = None
    ) -> LearnedArtifact:
        """Return a version or raise an artifact-not-found error."""

    @abstractmethod
    async def get_many(
        self, scope: TenantScope, artifact_ids: Sequence[UUID]
    ) -> Sequence[LearnedArtifact]:
        """Return current artifacts in requested order, rejecting missing IDs."""

    @abstractmethod
    async def query(self, query: ArtifactQuery) -> ArtifactPage:
        """Return a bounded page of artifacts matching the filters."""

    @abstractmethod
    async def versions(self, scope: TenantScope, artifact_id: UUID) -> Sequence[LearnedArtifact]:
        """Return the version history in ascending order."""

    @abstractmethod
    async def health(self) -> None:
        """Check repository availability and raise an error if it is unavailable."""


__all__ = ["ArtifactPage", "ArtifactQuery", "ArtifactRepository", "ProjectionIntent"]
