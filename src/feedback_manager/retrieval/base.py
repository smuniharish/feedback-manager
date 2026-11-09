"""Framework-neutral retrieval contract shared by learning and runtime adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from feedback_manager.retrieval.models import RetrievalRequest, RetrievalResult


class LearnedArtifactRetriever(ABC):
    """Retrieves scoped learned artifacts; `LearningEngine` is the bundled implementation."""

    @abstractmethod
    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        """Return the reviewed artifacts that apply to the request."""


__all__ = ["LearnedArtifactRetriever"]
