"""Bounded structured retrieval over the authoritative artifact repository."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from feedback_manager.learning.errors import LearningScopeError, UnsupportedCapabilityError
from feedback_manager.learning.models import ArtifactStatus
from feedback_manager.learning.scope import is_applicable
from feedback_manager.learning.validation import DefaultLearningValidationPolicy
from feedback_manager.retrieval.base import LearnedArtifactRetriever
from feedback_manager.retrieval.models import RetrievalRequest, RetrievalResult, RetrievedArtifact
from feedback_manager.storage.artifacts.base import ArtifactQuery

if TYPE_CHECKING:
    from feedback_manager.learning.validation import LearningValidationPolicy
    from feedback_manager.storage.artifacts.base import ArtifactRepository


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))


class StructuredRetriever(LearnedArtifactRetriever):
    """Retrieve active, reviewed artifacts using scoped structured filters."""

    def __init__(
        self,
        repository: ArtifactRepository,
        *,
        validation_policy: LearningValidationPolicy | None = None,
    ) -> None:
        self.repository = repository
        self.validation_policy = (
            validation_policy
            if validation_policy is not None
            else DefaultLearningValidationPolicy()
        )

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        """Return applicable artifacts with evidence-aware, explainable scores."""
        if request.graph_depth:
            raise UnsupportedCapabilityError("structured retrieval cannot traverse a graph")
        candidates: list[RetrievedArtifact] = []
        cursor = None
        examined = 0
        truncated = False
        seen_ids = set()
        query_tokens = _tokens(request.task)
        while examined < request.max_candidates:
            page_limit = min(50, request.max_candidates - examined)
            page = await self.repository.query(
                ArtifactQuery(
                    scope=request.scope,
                    statuses=(ArtifactStatus.ACTIVE,),
                    artifact_types=request.artifact_types,
                    enforce_visibility=True,
                    agent_id=request.agent_id,
                    agent_type=request.agent_type,
                    task_type=request.task_type,
                    tool=request.tool,
                    workflow=request.workflow,
                    limit=page_limit,
                    cursor=cursor,
                )
            )
            if len(page.artifacts) > page_limit:
                raise LearningScopeError("artifact repository exceeded the requested page size")
            examined += len(page.artifacts)
            for artifact in page.artifacts:
                if artifact.artifact_id in seen_ids:
                    raise LearningScopeError("artifact repository returned a duplicate artifact")
                seen_ids.add(artifact.artifact_id)
                if not is_applicable(
                    artifact.scope,
                    request.scope,
                    agent_id=request.agent_id,
                    agent_type=request.agent_type,
                    task_type=request.task_type,
                    tool=request.tool,
                    workflow=request.workflow,
                ):
                    raise LearningScopeError("artifact repository returned an out-of-scope result")
                if artifact.status is not ArtifactStatus.ACTIVE or (
                    request.artifact_types and artifact.artifact_type not in request.artifact_types
                ):
                    raise LearningScopeError("artifact repository returned an unrequested result")
                decision = self.validation_policy.evaluate(artifact)
                if not decision.eligible or decision.confidence.confidence < request.min_confidence:
                    continue
                tokens = _tokens(f"{artifact.title} {artifact.description} {artifact.content}")
                relevance = len(query_tokens & tokens) / len(query_tokens) if query_tokens else 0.0
                support = min(decision.confidence.independent_support / 2, 1.0)
                score = 0.5 * relevance + 0.3 * decision.confidence.confidence + 0.2 * support
                candidates.append(
                    RetrievedArtifact(
                        artifact=artifact,
                        relevance_score=score,
                        confidence_score=decision.confidence.confidence,
                        evidence_count=artifact.evidence_count,
                        explanation=(
                            f"structured token relevance: {relevance:.3f}",
                            f"independent support: {decision.confidence.independent_support}",
                            f"evidence confidence: {decision.confidence.confidence:.3f}",
                        ),
                    )
                )
            if page.next_cursor is None:
                break
            if not page.artifacts or page.next_cursor == cursor:
                raise LearningScopeError(
                    "artifact repository returned an invalid pagination cursor"
                )
            cursor = page.next_cursor
        else:
            truncated = cursor is not None

        candidates.sort(key=lambda item: (-item.relevance_score, str(item.artifact.artifact_id)))
        selected: list[RetrievedArtifact] = []
        context_size = 0
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
        return RetrievalResult(results=tuple(selected), truncated=truncated)


__all__ = ["StructuredRetriever"]
