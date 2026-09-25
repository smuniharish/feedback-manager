"""Feedback-side representation of upstream provenance/explainability data.

FeedbackManager consumes -- but does not reimplement -- ``langgraph-xai``'s
provenance model. :class:`FeedbackProvenanceReference` is the small, stable,
framework-independent shape that the rest of this package (and its public
API) works with. The translation from ``langgraph_xai`` canonical models
(``Execution``, ``Decision``, ``Evidence``, ``HumanInteraction``,
``ToolExecution``, ...) into this shape happens exclusively in
:mod:`feedback_manager.integrations.xai.adapter`, so the core domain never
imports ``langgraph_xai`` directly.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FeedbackProvenanceReference(BaseModel):
    """A summarized pointer into an upstream provenance/explainability system."""

    model_config = ConfigDict(frozen=True)

    provider: str
    """Identifies which provenance system produced this reference, e.g. ``"langgraph-xai"``."""

    execution_id: str | None = None
    decision_id: str | None = None
    evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    human_interaction_id: str | None = None
    tool_execution_id: str | None = None
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = ["FeedbackProvenanceReference"]
