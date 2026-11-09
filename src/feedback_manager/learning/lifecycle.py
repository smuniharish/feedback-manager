"""The independent lifecycle of a learned artifact."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from feedback_manager.learning.errors import ArtifactConflictError
from feedback_manager.learning.models import ArtifactStatus

if TYPE_CHECKING:
    from collections.abc import Mapping

LEGAL_ARTIFACT_TRANSITIONS: Mapping[ArtifactStatus, frozenset[ArtifactStatus]] = MappingProxyType(
    {
        ArtifactStatus.CANDIDATE: frozenset({ArtifactStatus.UNDER_REVIEW, ArtifactStatus.REJECTED}),
        ArtifactStatus.UNDER_REVIEW: frozenset(
            {ArtifactStatus.CANDIDATE, ArtifactStatus.VALIDATED, ArtifactStatus.REJECTED}
        ),
        ArtifactStatus.VALIDATED: frozenset(
            {ArtifactStatus.UNDER_REVIEW, ArtifactStatus.ACTIVE, ArtifactStatus.REJECTED}
        ),
        ArtifactStatus.ACTIVE: frozenset(
            {
                ArtifactStatus.UNDER_REVIEW,
                ArtifactStatus.DEPRECATED,
                ArtifactStatus.SUPERSEDED,
            }
        ),
        ArtifactStatus.DEPRECATED: frozenset(
            {ArtifactStatus.UNDER_REVIEW, ArtifactStatus.SUPERSEDED}
        ),
        ArtifactStatus.REJECTED: frozenset(),
        ArtifactStatus.SUPERSEDED: frozenset(),
    }
)


def validate_artifact_transition(current: ArtifactStatus, target: ArtifactStatus) -> None:
    """Validate an artifact lifecycle change, raising on illegal transitions."""
    if current != target and target not in LEGAL_ARTIFACT_TRANSITIONS[current]:
        raise ArtifactConflictError(
            f"illegal artifact lifecycle transition {current} -> {target}",
            current_status=current.value,
            requested_status=target.value,
        )


__all__ = ["LEGAL_ARTIFACT_TRANSITIONS", "validate_artifact_transition"]
