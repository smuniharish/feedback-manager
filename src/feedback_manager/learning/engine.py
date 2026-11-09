"""Bounded learning batches over replaceable authoritative artifact storage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from feedback_manager.learning.confidence import ConfidenceEngine
from feedback_manager.learning.errors import (
    ArtifactConflictError,
    ArtifactNotFoundError,
    LearningScopeError,
    LearningValidationError,
    UnsupportedCapabilityError,
)
from feedback_manager.learning.learners import (
    ConstraintLearner,
    KnowledgeLearner,
    OutcomePatternLearner,
    StrategyLearner,
)
from feedback_manager.learning.models import (
    ArtifactStatus,
    LearnedArtifact,
    SignalType,
    TenantScope,
    ValidationRecord,
)
from feedback_manager.learning.patterns import PatternMiner
from feedback_manager.learning.validation import DefaultLearningValidationPolicy
from feedback_manager.observability.operations import operation
from feedback_manager.retrieval.base import LearnedArtifactRetriever
from feedback_manager.retrieval.structured import StructuredRetriever
from feedback_manager.storage.artifacts.base import ArtifactQuery

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue

    from feedback_manager.learning.learners import ArtifactLearner
    from feedback_manager.learning.models import LearningSignal
    from feedback_manager.learning.validation import LearningValidationPolicy
    from feedback_manager.observability.operations import OperationObserver
    from feedback_manager.retrieval.models import RetrievalRequest, RetrievalResult
    from feedback_manager.storage.artifacts.base import ArtifactRepository


class LearningBatchResult(BaseModel):
    """Artifacts changed or observed and signal IDs without a configured learner."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifacts: tuple[LearnedArtifact, ...]
    unsupported_signal_ids: tuple[UUID, ...] = ()


class LearningEngine(LearnedArtifactRetriever):
    """Create and aggregate candidates without automatically trusting them.

    The host must authenticate scope and supply signals from trustworthy sources.
    Processing is bounded and replayable, but a batch is not an atomic transaction.
    """

    def __init__(
        self,
        repository: ArtifactRepository,
        *,
        validation_policy: LearningValidationPolicy | None = None,
        max_signals: int = 1000,
        retriever: LearnedArtifactRetriever | None = None,
        atomic_publication: bool = False,
        observer: OperationObserver | None = None,
    ) -> None:
        if atomic_publication and not repository.atomic_projection_intents:
            raise UnsupportedCapabilityError(
                "repository cannot atomically publish projection intents"
            )
        self.repository = repository
        self.atomic_publication = atomic_publication
        self.observer = observer
        self.validation_policy = (
            validation_policy
            if validation_policy is not None
            else DefaultLearningValidationPolicy()
        )
        self.confidence = ConfidenceEngine()
        self.miner = PatternMiner(max_signals=max_signals)
        outcome = OutcomePatternLearner(self.confidence)
        self._learners: dict[SignalType, ArtifactLearner] = {
            SignalType.CORRECTION: KnowledgeLearner(self.confidence),
            SignalType.IMPROVEMENT: StrategyLearner(self.confidence),
            SignalType.CONSTRAINT: ConstraintLearner(self.confidence),
            SignalType.SUCCESS: outcome,
            SignalType.FAILURE: outcome,
        }
        self._retriever = (
            retriever
            if retriever is not None
            else StructuredRetriever(repository, validation_policy=self.validation_policy)
        )

    async def process(self, signals: Sequence[LearningSignal]) -> LearningBatchResult:
        """Aggregate explicit signals into candidate artifacts.

        A conflict is retried using the latest version. Evidence already marked
        invalid is never silently restored by replaying an older signal.
        """
        with operation(self.observer, "learning.batch"):
            return await self._process(signals)

    async def _process(self, signals: Sequence[LearningSignal]) -> LearningBatchResult:
        artifacts: list[LearnedArtifact] = []
        unsupported: list[UUID] = []
        for pattern in self.miner.mine(signals):
            learner = self._learners.get(pattern.kind)
            if learner is None:
                unsupported.extend(signal.signal_id for signal in pattern.signals)
                continue
            artifacts.append(await self._merge(learner.learn(pattern)))
        return LearningBatchResult(
            artifacts=tuple(artifacts), unsupported_signal_ids=tuple(unsupported)
        )

    async def _merge(self, candidate: LearnedArtifact) -> LearnedArtifact:
        for _ in range(5):
            try:
                current = await self.repository.get(candidate.scope, candidate.artifact_id)
            except ArtifactNotFoundError:
                try:
                    return await self.repository.create(candidate)
                except ArtifactConflictError:
                    continue
            if current.scope != candidate.scope or current.artifact_type != candidate.artifact_type:
                raise ArtifactConflictError("existing artifact does not match the candidate")
            existing = {item.evidence_id: item for item in current.evidence}
            added = []
            candidate_ids = candidate.provenance.get("signal_ids")
            if not isinstance(candidate_ids, list) or len(candidate_ids) != len(candidate.evidence):
                raise ArtifactConflictError("candidate signal provenance is invalid")
            added_signal_ids: list[str] = []
            for item, signal_id in zip(candidate.evidence, candidate_ids, strict=True):
                if not isinstance(signal_id, str):
                    raise ArtifactConflictError("candidate signal provenance is invalid")
                previous = existing.get(item.evidence_id)
                if previous is None:
                    added.append(item)
                    added_signal_ids.append(signal_id)
                elif previous.model_copy(update={"valid": item.valid}) != item:
                    raise ArtifactConflictError("evidence ID has conflicting content")
            if not added:
                return current
            if current.status in (ArtifactStatus.REJECTED, ArtifactStatus.SUPERSEDED):
                raise ArtifactConflictError("cannot add evidence to a terminal artifact")
            evidence = (*current.evidence, *added)
            provenance = dict(current.provenance)
            stored_ids = provenance.get("signal_ids", [])
            if not isinstance(stored_ids, list):
                raise ArtifactConflictError("artifact signal provenance is invalid")
            signal_ids: list[str] = []
            for value in stored_ids:
                if not isinstance(value, str):
                    raise ArtifactConflictError("artifact signal provenance is invalid")
                signal_ids.append(value)
            updated_ids: list[JsonValue] = []
            updated_ids.extend(sorted(set(signal_ids) | set(added_signal_ids)))
            provenance["signal_ids"] = updated_ids
            status = current.status
            if status in (ArtifactStatus.ACTIVE, ArtifactStatus.VALIDATED):
                status = ArtifactStatus.UNDER_REVIEW
            updated = current.model_copy(
                update={
                    "evidence": evidence,
                    "confidence": self.confidence.score(evidence).confidence,
                    "provenance": provenance,
                    "status": status,
                    "version": current.version + 1,
                    "updated_at": max(
                        datetime.now(UTC), current.updated_at + timedelta(microseconds=1)
                    ),
                }
            )
            try:
                return await self._update(updated, expected_version=current.version)
            except ArtifactConflictError as exc:
                if exc.context.get("current_version") is None:
                    raise
        raise ArtifactConflictError("artifact changed repeatedly while learning")

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        """Retrieve active applicable artifacts using the configured retrieval strategy."""
        return await self._retriever.retrieve(request)

    async def request_review(self, scope: TenantScope, artifact_id: UUID) -> LearnedArtifact:
        """Move an artifact to review; the host must authorize the operation."""
        current = await self._get_scoped(scope, artifact_id)
        if current.status is ArtifactStatus.UNDER_REVIEW:
            return current
        updated = self._transition(current, ArtifactStatus.UNDER_REVIEW)
        return await self._update(updated, expected_version=current.version)

    async def review(
        self,
        scope: TenantScope,
        artifact_id: UUID,
        *,
        reviewer: str,
        approved: bool,
        reason: str = "",
    ) -> LearnedArtifact:
        """Record a human decision under the current learning validation policy.

        Reviewer identity is asserted by the authenticated host, not verified here.
        Approval is refused if the configured evidence policy is not satisfied.
        """
        current = await self._get_scoped(scope, artifact_id)
        if current.status is not ArtifactStatus.UNDER_REVIEW:
            raise ArtifactConflictError("artifact must be under review")
        policy = self.validation_policy
        decision = ValidationRecord(
            reviewer=reviewer,
            approved=approved,
            reason=reason,
            policy_version=policy.evaluate(current).policy_version,
            reviewed_version=current.version,
            artifact_digest=current.review_digest(),
        )
        updated = self._transition(
            current, ArtifactStatus.VALIDATED if approved else ArtifactStatus.REJECTED
        ).model_copy(update={"validation_history": (*current.validation_history, decision)})
        if approved:
            evaluation = policy.evaluate(updated)
            if not evaluation.eligible:
                raise LearningValidationError(
                    "artifact does not meet the learning validation policy",
                    reasons=evaluation.reasons,
                )
        return await self._update(updated, expected_version=current.version)

    async def activate(self, scope: TenantScope, artifact_id: UUID) -> LearnedArtifact:
        """Activate a validated artifact only while its policy remains satisfied."""
        current = await self._get_scoped(scope, artifact_id)
        if current.status is not ArtifactStatus.VALIDATED:
            raise ArtifactConflictError("artifact must be validated before activation")
        evaluation = self.validation_policy.evaluate(current)
        if not evaluation.eligible:
            raise LearningValidationError(
                "artifact does not meet the learning validation policy",
                reasons=evaluation.reasons,
            )
        return await self._update(
            self._transition(current, ArtifactStatus.ACTIVE), expected_version=current.version
        )

    async def invalidate_feedback(
        self, scope: TenantScope, feedback_id: UUID
    ) -> tuple[LearnedArtifact, ...]:
        """Invalidate evidence explicitly; callers must serialize source lifecycle changes.

        This process-local operation is not an atomic publication or durable tombstone.
        Returned artifacts retain invalid evidence for audit and active artifacts return
        to review. A failed operation may have already updated earlier artifacts.
        """
        cursor = None
        changed: list[LearnedArtifact] = []
        seen: set[UUID] = set()
        while True:
            page = await self.repository.query(
                ArtifactQuery(scope=scope, source_feedback_id=feedback_id, limit=50, cursor=cursor)
            )
            if len(page.artifacts) > 50:
                raise LearningScopeError("artifact repository exceeded the requested page size")
            for artifact in page.artifacts:
                if (
                    artifact.scope.tenant_id != scope.tenant_id
                    or artifact.scope.namespace != scope.namespace
                    or feedback_id not in artifact.source_feedback_ids
                    or artifact.artifact_id in seen
                ):
                    raise LearningScopeError("artifact repository returned an unrequested result")
                seen.add(artifact.artifact_id)
                changed.append(await self._invalidate_one(scope, artifact.artifact_id, feedback_id))
            if page.next_cursor is None:
                return tuple(changed)
            if not page.artifacts or page.next_cursor == cursor:
                raise LearningScopeError(
                    "artifact repository returned an invalid pagination cursor"
                )
            cursor = page.next_cursor

    async def _invalidate_one(
        self, scope: TenantScope, artifact_id: UUID, feedback_id: UUID
    ) -> LearnedArtifact:
        for _ in range(5):
            current = await self._get_scoped(scope, artifact_id)
            evidence = tuple(
                item.model_copy(update={"valid": False})
                if item.feedback_id == feedback_id
                else item
                for item in current.evidence
            )
            if evidence == current.evidence:
                return current
            if current.status in (ArtifactStatus.REJECTED, ArtifactStatus.SUPERSEDED):
                raise ArtifactConflictError("cannot invalidate evidence in a terminal artifact")
            status = current.status
            if status in (ArtifactStatus.ACTIVE, ArtifactStatus.VALIDATED):
                status = ArtifactStatus.UNDER_REVIEW
            updated = self._transition(current, status).model_copy(
                update={
                    "evidence": evidence,
                    "confidence": self.confidence.score(evidence).confidence,
                }
            )
            try:
                return await self._update(updated, expected_version=current.version)
            except ArtifactConflictError as exc:
                if exc.context.get("current_version") is None:
                    raise
        raise ArtifactConflictError("artifact changed repeatedly while invalidating evidence")

    async def _update(self, artifact: LearnedArtifact, *, expected_version: int) -> LearnedArtifact:
        with operation(self.observer, "database.artifact.write"):
            if self.atomic_publication:
                return await self.repository.update_with_projection(
                    artifact, expected_version=expected_version
                )
            return await self.repository.update(artifact, expected_version=expected_version)

    async def _get_scoped(self, scope: TenantScope, artifact_id: UUID) -> LearnedArtifact:
        artifact = await self.repository.get(scope, artifact_id)
        if (
            artifact.artifact_id != artifact_id
            or artifact.scope.tenant_id != scope.tenant_id
            or artifact.scope.namespace != scope.namespace
        ):
            raise LearningScopeError("artifact repository returned an out-of-scope result")
        return artifact

    @staticmethod
    def _transition(artifact: LearnedArtifact, status: ArtifactStatus) -> LearnedArtifact:
        return artifact.model_copy(
            update={
                "status": status,
                "version": artifact.version + 1,
                "updated_at": max(
                    datetime.now(UTC), artifact.updated_at + timedelta(microseconds=1)
                ),
            }
        )


__all__ = ["LearningBatchResult", "LearningEngine"]
