"""Structural contract for provenance adapters (e.g. langgraph-xai).

FeedbackManager does not implement provenance/explainability itself; it
*consumes* it from execution frameworks such as ``langgraph-xai`` through
this small ``Protocol``. Each framework integration provides its own
implementation (see :mod:`feedback_manager.integrations.xai.adapter`);
custom execution frameworks can plug in their own without touching core
FeedbackManager code.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from feedback_manager.core.context import CorrelationContext
from feedback_manager.core.provenance import FeedbackProvenanceReference


@runtime_checkable
class FeedbackProvenanceAdapter(Protocol):
    """Resolves provenance information for a given correlation context."""

    async def resolve(
        self, correlation: CorrelationContext
    ) -> FeedbackProvenanceReference | None: ...


__all__ = ["FeedbackProvenanceAdapter"]
