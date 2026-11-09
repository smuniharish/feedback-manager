"""Requests and explainable results for learned-artifact retrieval."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from feedback_manager.core._types import Identifier
from feedback_manager.learning.models import ArtifactType, LearnedArtifact, TenantScope


class RetrievalRequest(BaseModel):
    """A bounded request for artifacts applicable to one tenant and agent."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    scope: TenantScope
    agent_id: Identifier | None = None
    agent_type: Identifier | None = None
    task: str = Field(default="", max_length=10000)
    task_type: Identifier | None = None
    tool: Identifier | None = None
    workflow: Identifier | None = None
    artifact_types: tuple[ArtifactType, ...] = ()
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    limit: int = Field(default=10, ge=1, le=100)
    max_candidates: int = Field(default=200, ge=1, le=1000)
    max_context_chars: int = Field(default=10000, ge=1, le=100000)
    graph_depth: int = Field(default=0, ge=0, le=10)


class RetrievedArtifact(BaseModel):
    """An authoritative artifact and the factors used to rank it."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    artifact: LearnedArtifact
    relevance_score: float = Field(ge=0.0, le=1.0)
    confidence_score: float = Field(ge=0.0, le=1.0)
    evidence_count: int = Field(ge=0)
    explanation: tuple[str, ...]


class RetrievalResult(BaseModel):
    """The bounded results and whether candidates or context were truncated."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    results: tuple[RetrievedArtifact, ...]
    truncated: bool = False

    @property
    def artifacts(self) -> tuple[LearnedArtifact, ...]:
        """Return the artifacts in ranked order."""
        return tuple(result.artifact for result in self.results)

    def as_context(self) -> str:
        """Render only artifact titles and content as untrusted context data."""
        return "\n\n".join(
            f"{result.artifact.title}\n{result.artifact.content}" for result in self.results
        )


__all__ = ["RetrievalRequest", "RetrievalResult", "RetrievedArtifact"]
