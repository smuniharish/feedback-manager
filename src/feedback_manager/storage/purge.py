"""Coordinated, retry-safe purge of one experience and the records derived from it.

`ExperiencePurger` composes the public experience, assessment, payload and
learning contracts. It is not a distributed transaction: each step is
idempotent, and a failed purge is completed by calling `purge` again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from feedback_manager._logging import get_logger
from feedback_manager.learning.models import TenantScope
from feedback_manager.storage.assessment import AssessmentRepositoryABC
from feedback_manager.storage.experience import ExperienceNotFoundError, ExperienceRepositoryABC
from feedback_manager.storage.payload import PayloadScope, PayloadStoreABC, _check_scope

if TYPE_CHECKING:
    from feedback_manager.assessment import FeedbackAssessment, LearningDecision
    from feedback_manager.experience import ExecutionExperience
    from feedback_manager.learning.engine import LearningEngine
    from feedback_manager.storage.payload import PayloadMetadata

_logger = get_logger("feedback_manager.storage.purge")
_PAGE = 1000


class PurgeReport(BaseModel):
    """What one `ExperiencePurger.purge` call removed or invalidated."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    experience_id: UUID
    experience_deleted: bool
    assessments_deleted: int = 0
    decisions_deleted: int = 0
    payloads_deleted: int = 0
    feedback_ids: tuple[UUID, ...] = ()
    artifact_ids: tuple[UUID, ...] = ()


class ExperiencePurger:
    """Delete an experience with its assessments, decisions and payloads.

    The order makes retries safe: read everything, invalidate learned evidence
    from the related feedback, delete payloads, delete assessments and decisions,
    and delete the experience last so a retry can still find its references.

    Feedback itself is never deleted: it stays authoritative. Artifacts are not
    rewritten; their evidence from the related feedback is marked invalid, which
    returns active artifacts to review and drops them from retrieval. Payloads
    must not be shared between experiences. Assessments of other experiences
    that cite this one as evidence fail later verification instead of being
    removed. Without ``assessments`` or ``engine`` those steps are skipped.
    """

    def __init__(
        self,
        experiences: ExperienceRepositoryABC,
        payloads: PayloadStoreABC,
        *,
        assessments: AssessmentRepositoryABC | None = None,
        engine: LearningEngine | None = None,
    ) -> None:
        if not isinstance(experiences, ExperienceRepositoryABC):
            raise TypeError("experiences must implement ExperienceRepositoryABC")
        if not isinstance(payloads, PayloadStoreABC):
            raise TypeError("payloads must implement PayloadStoreABC")
        if assessments is not None and not isinstance(assessments, AssessmentRepositoryABC):
            raise TypeError("assessments must implement AssessmentRepositoryABC")
        self.experiences = experiences
        self.payloads = payloads
        self.assessments = assessments
        self.engine = engine

    async def purge(self, scope: PayloadScope, experience_id: UUID) -> PurgeReport:
        """Purge one scoped experience; repeat the call to finish a failed purge."""
        scope = _check_scope(scope)
        if not isinstance(experience_id, UUID):
            raise TypeError("experience_id must be a UUID")
        versions = await self._experience_versions(scope, experience_id)
        assessments, decisions = await self._assessment_records(scope, experience_id)
        feedback_ids = tuple(
            dict.fromkeys(
                (
                    *(item for version in versions for item in version.feedback_ids),
                    *(assessment.feedback_id for assessment in assessments),
                )
            )
        )
        artifact_ids: dict[UUID, None] = {}
        if self.engine is not None:
            tenant = TenantScope(tenant_id=scope.tenant_id, namespace=scope.namespace)
            for feedback_id in feedback_ids:
                for artifact in await self.engine.invalidate_feedback(tenant, feedback_id):
                    artifact_ids[artifact.artifact_id] = None
        references: dict[tuple[PayloadScope, UUID], PayloadMetadata] = {}
        for reference in (
            *(item for version in versions for item in version.payload_references()),
            *(item for assessment in assessments for item in assessment.payload_references()),
            *(decision.reason for decision in decisions if decision.reason is not None),
        ):
            references.setdefault((reference.scope, reference.payload_id), reference)
        payloads_deleted = 0
        for reference_scope, payload_id in references:
            payloads_deleted += await self.payloads.delete(reference_scope, payload_id)
        assessments_deleted = decisions_deleted = 0
        if self.assessments is not None:
            deletion = await self.assessments.delete_for_experience(scope, experience_id)
            assessments_deleted = len(deletion.assessments)
            decisions_deleted = len(deletion.decisions)
        deleted = await self.experiences.delete(scope, experience_id)
        report = PurgeReport(
            experience_id=experience_id,
            experience_deleted=deleted,
            assessments_deleted=assessments_deleted,
            decisions_deleted=decisions_deleted,
            payloads_deleted=payloads_deleted,
            feedback_ids=feedback_ids,
            artifact_ids=tuple(artifact_ids),
        )
        _logger.info(
            "experience purged",
            experience_id=str(experience_id),
            experience_deleted=deleted,
            assessments_deleted=assessments_deleted,
            decisions_deleted=decisions_deleted,
            payloads_deleted=payloads_deleted,
            artifacts_invalidated=len(artifact_ids),
        )
        return report

    async def _experience_versions(
        self, scope: PayloadScope, experience_id: UUID
    ) -> tuple[ExecutionExperience, ...]:
        found: list[ExecutionExperience] = []
        try:
            while True:
                page = await self.experiences.versions(
                    scope, experience_id, after_version=len(found), limit=_PAGE
                )
                found.extend(page)
                if len(page) < _PAGE:
                    return tuple(found)
        except ExperienceNotFoundError:
            return tuple(found)

    async def _assessment_records(
        self, scope: PayloadScope, experience_id: UUID
    ) -> tuple[tuple[FeedbackAssessment, ...], tuple[LearningDecision, ...]]:
        repository = self.assessments
        if repository is None:
            return (), ()
        assessments: list[FeedbackAssessment] = []
        decisions: list[LearningDecision] = []
        cursor: UUID | None = None
        while True:
            page = await repository.for_experience(scope, experience_id, limit=_PAGE, cursor=cursor)
            for current in page.assessments:
                history: list[FeedbackAssessment] = []
                while True:
                    batch = await repository.versions(
                        scope, current.assessment_id, after_version=len(history), limit=_PAGE
                    )
                    history.extend(batch)
                    if len(batch) < _PAGE:
                        break
                assessments.extend(history)
                for item in history:
                    decision = await repository.decision(
                        scope, item.assessment_id, assessment_version=item.version
                    )
                    if decision is not None:
                        decisions.append(decision)
            if page.next_cursor is None:
                return tuple(assessments), tuple(decisions)
            cursor = page.next_cursor


__all__ = ["ExperiencePurger", "PurgeReport"]
