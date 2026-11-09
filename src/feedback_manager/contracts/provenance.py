"""The provenance contract: link new feedback to the execution it is about."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from feedback_manager.core.events import FeedbackEvent
    from feedback_manager.core.provenance import FeedbackProvenanceReference


class ProvenanceProvider(ABC):
    """Resolves where a piece of feedback came from in an execution record system.

    `FeedbackManager` calls `resolve` for each new feedback event, after
    redaction and correlation and before storage, when it is constructed with
    ``provenance_provider=``. The returned reference is stored on the event.

    feedback-manager ships `AgxaiProvenanceProvider` for ``agxai`` runtimes.
    Implement this class to read provenance from another tracing or
    explainability system. Implementations must not capture provenance
    themselves, must not modify the feedback, and should return ``None`` when
    no matching execution is known. A raised exception is handled by the
    manager's failure policy for the ``PROVENANCE`` stage.
    """

    @abstractmethod
    async def resolve(self, feedback: FeedbackEvent) -> FeedbackProvenanceReference | None:
        """Return the provenance of ``feedback``, or ``None`` when no execution matches."""


__all__ = ["ProvenanceProvider"]
