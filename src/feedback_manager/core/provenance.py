"""A pointer from feedback into the ``langgraph-xai`` provenance records of a run."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from feedback_manager.core._types import Identifier, JsonObject


class FeedbackProvenanceReference(BaseModel):
    """The ``langgraph-xai`` records a piece of feedback refers to.

    `FeedbackManager` attaches it at submission when it was given an
    ``XAIRuntime``. The IDs locate the records in the runtime's provenance store.

    Attributes:
        run_id: The ``langgraph-xai`` run ID.
        execution_id: The ID of the run's ``Execution`` record.
        summary: A one-line, human-readable description of the run.
        node_execution_id: The latest recorded execution of the feedback's node
            (``ExecutionContext.node_id``) in the run. ``langgraph-xai`` records
            a node execution when the node finishes, so feedback submitted from
            inside the node itself has none yet.
        tool_execution_id: The tool execution matching the feedback's
            ``ExecutionContext.tool_call_id``.
        human_interaction_id: The human interaction matching the feedback's
            ``ExecutionContext.interrupt_id``.
        decision_id: The latest decision recorded in the run when the feedback
            was submitted. Only set for feedback submitted while the run is
            active, because ``langgraph-xai`` keeps decisions in the live run and
            delivers them to plugins instead of storing them.
        evidence_ids: The evidence that decision relied on or, without a
            decision, all evidence recorded so far. Like ``decision_id``, only
            set while the run is active.
        metadata: Additional details, such as the run status and record counts.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    run_id: Identifier
    execution_id: Identifier
    summary: Identifier
    node_execution_id: Identifier | None = None
    tool_execution_id: Identifier | None = None
    human_interaction_id: Identifier | None = None
    decision_id: Identifier | None = None
    evidence_ids: tuple[Identifier, ...] = ()
    metadata: JsonObject = Field(default_factory=dict)


__all__ = ["FeedbackProvenanceReference"]
