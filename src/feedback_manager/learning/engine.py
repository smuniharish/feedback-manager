"""Bounded learning batches over replaceable authoritative artifact storage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, UUID, uuid5

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
    ArtifactType,
    LearnedArtifact,
    SignalType,
    TenantScope,
    ValidationRecord,
)
from feedback_manager.learning.patterns import PatternMiner
from feedback_manager.learning.scope import is_applicable
from feedback_manager.learning.synthesis import (
    DeterministicSynthesizer,
    PatternSupport,
    SynthesisInput,
    cluster_patterns,
    validate_draft,
)
from feedback_manager.learning.validation import DefaultLearningValidationPolicy
from feedback_manager.observability.operations import operation
from feedback_manager.retrieval.base import LearnedArtifactRetriever
from feedback_manager.retrieval.retrievalloom import RetrievalLoomRetriever
from feedback_manager.retrieval.structured import StructuredRetriever
from feedback_manager.storage.artifacts.base import ArtifactQuery

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue

    from feedback_manager.learning.assessment_verification import AssessmentVerifier
    from feedback_manager.learning.learners import ArtifactLearner
    from feedback_manager.learning.models import LearningSignal
    from feedback_manager.learning.patterns import PatternSimilarity
    from feedback_manager.learning.synthesis import PatternSynthesizer
    from feedback_manager.learning.validation import LearningValidationPolicy
    from feedback_manager.observability.operations import OperationObserver
    from feedback_manager.retrieval.models import RetrievalRequest, RetrievalResult
    from feedback_manager.storage.artifacts.base import ArtifactRepository


_SYNTHESIS_TYPES = {
    SignalType.CORRECTION: ArtifactType.KNOWLEDGE,
    SignalType.IMPROVEMENT: ArtifactType.STRATEGY,
    SignalType.CONSTRAINT: ArtifactType.CONSTRAINT,
    SignalType.SUCCESS: ArtifactType.PATTERN,
    SignalType.FAILURE: ArtifactType.PATTERN,
}


class LearningBatchResult(BaseModel):
    """Artifacts changed or observed and signal IDs without a configured learner."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifacts: tuple[LearnedArtifact, ...]
    unsupported_signal_ids: tuple[UUID, ...] = ()


class LearningEngine(LearnedArtifactRetriever):
    """Create and aggregate candidates without automatically trusting them.

    The host must authenticate scope and supply signals from trustworthy sources.
    Feedback-derived signals require a current, independently accepted assessment.
    Processing is bounded and replayable, but a batch is not an atomic transaction.
    """

    def __init__(
        self,
        repository: ArtifactRepository,
        *,
        validation_policy: LearningValidationPolicy | None = None,
        max_signals: int = 1000,
        retriever: LearnedArtifactRetriever | None = None,
        assessment_verifier: AssessmentVerifier | None = None,
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
        self.assessment_verifier = assessment_verifier
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
            else RetrievalLoomRetriever(
                repository,
                validation_policy=self.validation_policy,
                assessment_verifier=assessment_verifier,
            )
        )
        self._retrieval_guard = StructuredRetriever(
            repository,
            validation_policy=self.validation_policy,
            assessment_verifier=assessment_verifier,
        )

    async def process(self, signals: Sequence[LearningSignal]) -> LearningBatchResult:
        """Aggregate verified feedback and explicit outcomes into candidates.

        A conflict is retried using the latest version. Evidence already marked
        invalid is never silently restored by replaying an older signal.
        Cross-store verification and artifact writes are not one transaction.
        """
        with operation(self.observer, "learning.batch"):
            return await self._process(signals)

    async def _verify_signals(self, signals: Sequence[LearningSignal]) -> None:
        for signal in signals:
            if signal.evidence.feedback_id is None:
                continue
            if self.assessment_verifier is None:
                raise LearningValidationError("feedback learning requires an assessment verifier")
            verification = await self.assessment_verifier.verify_signal(signal)
            if not verification.eligible:
                raise LearningValidationError(
                    f"feedback assessment verification failed: {verification.reason}"
                )

    async def synthesize(
        self,
        signals: Sequence[LearningSignal],
        *,
        synthesizer: PatternSynthesizer | None = None,
        similarities: Sequence[PatternSimilarity] | None = None,
        threshold: float = 0.8,
    ) -> LearningBatchResult:
        """Combine related, independently supported patterns into review candidates.

        Patterns are related by ``similarities`` when supplied (for example from
        RetrievalLoom), otherwise by ``PatternMiner.similar``. Each cluster shares one
        scope and signal kind, so synthesis never widens applicability. Contradicting
        evidence is retained but never counted as support. Results are candidates.
        """
        with operation(self.observer, "learning.synthesis"):
            await self._verify_signals(signals)
            patterns = self.miner.mine(signals)
            suggestions = (
                self.miner.similar(patterns, threshold=threshold)
                if similarities is None
                else similarities
            )
            selected = synthesizer if synthesizer is not None else DeterministicSynthesizer()
            artifacts: list[LearnedArtifact] = []
            unsupported: list[UUID] = []
            for cluster in cluster_patterns(patterns, suggestions):
                artifact_type = _SYNTHESIS_TYPES.get(cluster[0].kind)
                if artifact_type is None:
                    unsupported.extend(s.signal_id for p in cluster for s in p.signals)
                    continue
                request = SynthesisInput(
                    scope=cluster[0].scope,
                    kind=cluster[0].kind,
                    patterns=tuple(PatternSupport.of(pattern) for pattern in cluster),
                )
                draft = await selected.synthesize(request)
                cited = validate_draft(request, draft)
                ordered = sorted(
                    (signal for item in cited for signal in item.pattern.signals),
                    key=lambda signal: signal.signal_id.hex,
                )
                evidence = tuple(signal.evidence for signal in ordered)
                pattern_ids = sorted(item.pattern.pattern_id for item in cited)
                candidate = LearnedArtifact(
                    artifact_id=uuid5(
                        NAMESPACE_URL,
                        f"feedback-manager:synthesis:{selected.name}:{','.join(pattern_ids)}",
                    ),
                    artifact_type=artifact_type,
                    title=draft.title,
                    description=(
                        f"Candidate synthesized by {selected.name} from "
                        f"{len(pattern_ids)} related patterns; requires review."
                    ),
                    content=draft.content,
                    scope=request.scope,
                    evidence=evidence,
                    confidence=self.confidence.score(evidence).confidence,
                    provenance={
                        "signal_ids": [str(signal.signal_id) for signal in ordered],
                        "pattern_ids": list(pattern_ids),
                        "synthesizer": selected.name,
                        "contradicting_signal_ids": [
                            str(signal.signal_id)
                            for signal in ordered
                            if signal.evidence.contradicts
                        ],
                    },
                )
                artifacts.append(await self._merge(candidate))
            return LearningBatchResult(
                artifacts=tuple(artifacts), unsupported_signal_ids=tuple(unsupported)
            )

    async def _process(self, signals: Sequence[LearningSignal]) -> LearningBatchResult:
        artifacts: list[LearnedArtifact] = []
        unsupported: list[UUID] = []
        patterns = self.miner.mine(signals)
        await self._verify_signals(signals)
        for pattern in patterns:
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
                    created = await self.repository.create(candidate)
                    await self._project(created)
                    return created
                except ArtifactConflictError:
                    continue
            if current.scope != candidate.scope or current.artifact_type != candidate.artifact_type:
                raise ArtifactConflictError("existing artifact does not match the candidate")
            existing = {item.evidence_id: item for item in current.evidence}
            added = []
            reassessed = {}
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
                else:
                    comparable = previous.model_copy(
                        update={
                            "valid": item.valid,
                            "assessment_id": item.assessment_id,
                            "assessment_version": item.assessment_version,
                        }
                    )
                    if comparable != item:
                        raise ArtifactConflictError("evidence ID has conflicting content")
                    if (
                        previous.assessment_id != item.assessment_id
                        or previous.assessment_version != item.assessment_version
                    ):
                        reassessed[item.evidence_id] = previous.model_copy(
                            update={
                                "assessment_id": item.assessment_id,
                                "assessment_version": item.assessment_version,
                            }
                        )
            if not added and not reassessed:
                return current
            if current.status in (ArtifactStatus.REJECTED, ArtifactStatus.SUPERSEDED):
                raise ArtifactConflictError("cannot add evidence to a terminal artifact")
            evidence = tuple(reassessed.get(item.evidence_id, item) for item in current.evidence)
            evidence = (*evidence, *added)
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
        result = await self._retriever.retrieve(request)
        selected = []
        for item in result.results:
            artifact = item.artifact
            if (
                artifact.status is not ArtifactStatus.ACTIVE
                or not is_applicable(
                    artifact.scope,
                    request.scope,
                    agent_id=request.agent_id,
                    agent_type=request.agent_type,
                    task_type=request.task_type,
                    tool=request.tool,
                    workflow=request.workflow,
                )
                or (request.artifact_types and artifact.artifact_type not in request.artifact_types)
            ):
                raise LearningScopeError("retriever returned an unrequested artifact")
            decision = self.validation_policy.evaluate(artifact)
            if (
                decision.eligible
                and decision.confidence.confidence >= request.min_confidence
                and await self._retrieval_guard.current_and_assessed(artifact)
            ):
                selected.append(item)
        return result.model_copy(update={"results": tuple(selected)})

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
        if approved and not await self._retrieval_guard.current_and_assessed(current):
            raise LearningValidationError("artifact assessment verification failed")
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
        if not await self._retrieval_guard.current_and_assessed(current):
            raise LearningValidationError("artifact assessment verification failed")
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
                updated = await self.repository.update_with_projection(
                    artifact, expected_version=expected_version
                )
            else:
                updated = await self.repository.update(artifact, expected_version=expected_version)
        await self._project(updated)
        return updated

    async def _project(self, artifact: LearnedArtifact) -> None:
        if isinstance(self._retriever, RetrievalLoomRetriever):
            await self._retriever.project(artifact)

    async def aclose(self) -> None:
        """Close the default retrieval engine; injected retrievers remain caller-owned."""
        if isinstance(self._retriever, RetrievalLoomRetriever):
            await self._retriever.aclose()

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
