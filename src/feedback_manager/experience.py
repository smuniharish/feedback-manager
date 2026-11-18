"""Immutable records of observable execution experiences."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from feedback_manager.core._types import Identifier
from feedback_manager.storage.payload import PayloadMetadata, PayloadScope


def _now() -> datetime:
    return datetime.now(UTC)


class ObservableDecision(BaseModel):
    """A decision explicitly exposed by the execution, not hidden reasoning."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    decision_id: UUID = Field(default_factory=uuid4)
    execution_id: Identifier
    sequence: int = Field(ge=0, strict=True)
    decision_type: Identifier
    context: PayloadMetadata | None = None
    selected_action: PayloadMetadata | None = None
    alternatives: PayloadMetadata | None = None
    explanation: PayloadMetadata | None = None
    output: PayloadMetadata | None = None
    tool_interaction_ids: tuple[UUID, ...] = ()
    occurred_at: AwareDatetime | None = None
    provenance: PayloadMetadata | None = None


class ObservableToolInteraction(BaseModel):
    """A tool call and its observable input, output or failure."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    interaction_id: UUID = Field(default_factory=uuid4)
    tool_name: Identifier
    status: Identifier | None = None
    attempt: int = Field(default=1, ge=1, strict=True)
    input: PayloadMetadata | None = None
    output: PayloadMetadata | None = None
    failure: PayloadMetadata | None = None
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    provenance: PayloadMetadata | None = None

    @model_validator(mode="after")
    def _validate_times(self) -> ObservableToolInteraction:
        if (
            self.started_at is not None
            and self.completed_at is not None
            and self.completed_at < self.started_at
        ):
            raise ValueError("tool completion precedes its start")
        return self


class ExecutionExperience(BaseModel):
    """One immutable version of an observable execution episode.

    Structured inputs, outputs and metadata are retained as scoped payload
    references, rather than duplicated inside the archive record. The host
    supplies and authorizes the scope and decides what observable data to
    capture; hidden chain-of-thought is not part of this model.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    experience_id: UUID = Field(default_factory=uuid4)
    scope: PayloadScope
    execution_id: Identifier
    parent_execution_id: Identifier | None = None
    session_id: Identifier | None = None
    agent_version: Identifier | None = None
    framework: Identifier | None = None
    framework_version: Identifier | None = None
    task_type: Identifier | None = None
    status: Identifier | None = None
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    original_input: PayloadMetadata | None = None
    normalized_input: PayloadMetadata | None = None
    final_output: PayloadMetadata | None = None
    structured_output: PayloadMetadata | None = None
    execution_metadata: PayloadMetadata | None = None
    model_metadata: PayloadMetadata | None = None
    provenance: PayloadMetadata | None = None
    decisions: tuple[ObservableDecision, ...] = ()
    tool_interactions: tuple[ObservableToolInteraction, ...] = ()
    feedback_ids: tuple[UUID, ...] = ()
    assessment_ids: tuple[UUID, ...] = ()
    outcome_ids: tuple[UUID, ...] = ()
    schema_version: Identifier = "1"
    version: int = Field(default=1, ge=1, strict=True)
    created_at: AwareDatetime = Field(default_factory=_now)
    updated_at: AwareDatetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def _validate_experience(self) -> ExecutionExperience:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        if (
            self.started_at is not None
            and self.completed_at is not None
            and self.completed_at < self.started_at
        ):
            raise ValueError("execution completion precedes its start")
        if any(decision.execution_id != self.execution_id for decision in self.decisions):
            raise ValueError("decision execution_id must match the experience")
        if len({decision.decision_id for decision in self.decisions}) != len(self.decisions):
            raise ValueError("decision IDs must be unique")
        if len({decision.sequence for decision in self.decisions}) != len(self.decisions):
            raise ValueError("decision sequences must be unique")
        if len({tool.interaction_id for tool in self.tool_interactions}) != len(
            self.tool_interactions
        ):
            raise ValueError("tool interaction IDs must be unique")
        if any(reference.scope != self.scope for reference in self.payload_references()):
            raise ValueError("payload scope must match the experience scope")
        return self

    def payload_references(self) -> tuple[PayloadMetadata, ...]:
        """Return every payload referenced by this version, in field order."""
        references = [
            self.original_input,
            self.normalized_input,
            self.final_output,
            self.structured_output,
            self.execution_metadata,
            self.model_metadata,
            self.provenance,
        ]
        for decision in self.decisions:
            references.extend(
                (
                    decision.context,
                    decision.selected_action,
                    decision.alternatives,
                    decision.explanation,
                    decision.output,
                    decision.provenance,
                )
            )
        for tool in self.tool_interactions:
            references.extend((tool.input, tool.output, tool.failure, tool.provenance))
        return tuple(reference for reference in references if reference is not None)


__all__ = ["ExecutionExperience", "ObservableDecision", "ObservableToolInteraction"]
