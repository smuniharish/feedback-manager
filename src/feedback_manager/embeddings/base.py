"""A provider contract for document and query embeddings."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from feedback_manager.embeddings.models import EmbeddingSpace


class EmbeddingProvider(ABC):
    """Embeds documents and queries in one explicitly identified space."""

    @property
    @abstractmethod
    def space(self) -> EmbeddingSpace:
        """Describe the provider's embedding space."""

    @abstractmethod
    async def embed_documents(self, documents: Sequence[str]) -> Sequence[Sequence[float]]:
        """Embed documents in input order."""

    @abstractmethod
    async def embed_query(self, query: str) -> Sequence[float]:
        """Embed a query using the model's query-specific instruction."""

    @abstractmethod
    async def health(self) -> None:
        """Check provider availability, raising an error when unavailable."""


__all__ = ["EmbeddingProvider"]
