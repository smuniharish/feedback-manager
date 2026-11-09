"""Vector storage contracts, independent of embedding providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from feedback_manager.core._types import Identifier, JsonObject
from feedback_manager.embeddings.models import EmbeddingSpace
from feedback_manager.learning.errors import UnsupportedCapabilityError
from feedback_manager.learning.models import TenantScope

if TYPE_CHECKING:
    from collections.abc import Sequence


class VectorCapabilities(BaseModel):
    """Optional operations a vector adapter supports."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metadata_filtering: bool = False
    pagination: bool = False
    index_management: bool = False
    full_text: bool = False
    hybrid_search: bool = False


class VectorRecord(BaseModel):
    """One scoped projection of an artifact version."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    record_id: Identifier
    scope: TenantScope
    artifact_id: UUID
    artifact_version: int = Field(ge=1)
    space: EmbeddingSpace
    vector: tuple[float, ...]
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_dimensions(self) -> VectorRecord:
        if len(self.vector) != self.space.dimensions:
            raise ValueError("vector dimensions do not match the embedding space")
        return self


class VectorMatch(BaseModel):
    """A matching vector and its backend-defined distance."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    record: VectorRecord
    distance: float = Field(ge=0)


class VectorStore(ABC):
    """Store tenant-scoped vector projections in isolated embedding spaces."""

    @property
    @abstractmethod
    def capabilities(self) -> VectorCapabilities:
        """Describe optional operations supported by this adapter."""

    async def upsert(self, record: VectorRecord) -> None:
        """Idempotently insert or replace one projection."""
        await self.upsert_many((record,))

    @abstractmethod
    async def upsert_many(self, records: Sequence[VectorRecord]) -> None:
        """Idempotently insert or replace a batch of projections."""

    @abstractmethod
    async def get(
        self, scope: TenantScope, space: EmbeddingSpace, record_ids: Sequence[str]
    ) -> Sequence[VectorRecord]:
        """Return the scoped projections present for the requested IDs."""

    @abstractmethod
    async def search(
        self,
        scope: TenantScope,
        space: EmbeddingSpace,
        vector: Sequence[float],
        *,
        limit: int,
        metadata: JsonObject | None = None,
    ) -> Sequence[VectorMatch]:
        """Search one embedding space, filtering by scope before ranking."""

    @abstractmethod
    async def delete(
        self, scope: TenantScope, space: EmbeddingSpace, record_ids: Sequence[str]
    ) -> None:
        """Idempotently remove the scoped projections with these IDs."""

    async def create_index(self, scope: TenantScope, space: EmbeddingSpace) -> None:
        """Create a vector index when the adapter supports index management."""
        raise UnsupportedCapabilityError(
            f"vector index management is not supported for namespace {scope.namespace!r} "
            f"and embedding space {space.space_id}"
        )

    @abstractmethod
    async def health(self) -> None:
        """Check vector storage availability and raise on failure."""


__all__ = ["VectorCapabilities", "VectorMatch", "VectorRecord", "VectorStore"]
