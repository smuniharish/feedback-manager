"""Shared invariants for authoritative version histories."""

from __future__ import annotations

from typing import TYPE_CHECKING

from feedback_manager.learning.errors import ArtifactConflictError
from feedback_manager.learning.lifecycle import validate_artifact_transition
from feedback_manager.learning.models import ArtifactStatus

if TYPE_CHECKING:
    from collections.abc import Sequence

    from feedback_manager.learning.models import LearnedArtifact


def validate_update(history: Sequence[LearnedArtifact], updated: LearnedArtifact) -> None:
    """Reject version, lifecycle, history and review-integrity violations."""
    current = history[-1]
    if updated.version != current.version + 1:
        raise ArtifactConflictError("artifact version must increase by one")
    if updated.created_at != current.created_at or updated.updated_at <= current.updated_at:
        raise ArtifactConflictError("artifact timestamps are not consistent")
    if updated.artifact_type != current.artifact_type:
        raise ArtifactConflictError("artifact type cannot change")
    if current.status in (ArtifactStatus.REJECTED, ArtifactStatus.SUPERSEDED):
        raise ArtifactConflictError("terminal artifacts cannot be changed")
    if updated.validation_history[: len(current.validation_history)] != current.validation_history:
        raise ArtifactConflictError("validation history cannot be rewritten")
    validate_artifact_transition(current.status, updated.status)
    protected = ("title", "description", "content", "scope", "evidence", "confidence")
    if (
        current.status in (ArtifactStatus.VALIDATED, ArtifactStatus.ACTIVE)
        and updated.status in (ArtifactStatus.VALIDATED, ArtifactStatus.ACTIVE)
        and any(getattr(current, field) != getattr(updated, field) for field in protected)
    ):
        raise ArtifactConflictError("reviewed artifact changes require a new review")
    if updated.status in (ArtifactStatus.VALIDATED, ArtifactStatus.ACTIVE):
        if not updated.validation_history or not updated.validation_history[-1].approved:
            raise ArtifactConflictError("validated artifacts require an approved review")
        review = updated.validation_history[-1]
        if (
            review.reviewed_version > current.version
            or history[review.reviewed_version - 1].review_digest() != review.artifact_digest
            or updated.review_digest() != review.artifact_digest
        ):
            raise ArtifactConflictError("artifact review does not match the reviewed content")
