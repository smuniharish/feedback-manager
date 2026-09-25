"""Contract for provenance adapters.

FeedbackManager does not implement provenance/explainability itself; it
*consumes* it from an execution framework. ``langgraph-xai`` is the only
provenance source this package ships an adapter for --
:class:`feedback_manager.integrations.xai.adapter.XAIProvenanceAdapter` --
and it is what you should use directly in almost every case. This
``Protocol`` exists only so the core domain and ``FeedbackManager`` do not
import ``langgraph_xai`` types directly (Section 40 of the design spec);
it is not meant to invite a menagerie of alternative provenance sources.
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
