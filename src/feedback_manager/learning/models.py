"""Public, framework-independent learning data contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from feedback_manager.core._types import Identifier, JsonObject
from feedback_manager.embeddings.models import EmbeddingSpace


def _now() -> datetime:
    return datetime.now(UTC)


class ArtifactType(StrEnum):
    """The kind of reusable learning represented by an artifact."""

    KNOWLEDGE = "knowledge"
    STRATEGY = "strategy"
    CONSTRAINT = "constraint"
    PATTERN = "pattern"


class ArtifactStatus(StrEnum):
    """A learned artifact's independent review and publication lifecycle."""

    CANDIDATE = "candidate"
    UNDER_REVIEW = "under_review"
    VALIDATED = "validated"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ScopeVisibility(StrEnum):
    """Who can use an artifact within its tenant and namespace."""

    AGENT = "agent"
    AGENT_TYPE = "agent_type"
    TENANT = "tenant"


class SignalType(StrEnum):
    """The normalized observation represented by a learning signal."""

    CORRECTION = "correction"
    APPROVAL = "approval"
    REJECTION = "rejection"
    FAILURE = "failure"
    SUCCESS = "success"
    IMPROVEMENT = "improvement"
    CONSTRAINT = "constraint"


class TenantScope(BaseModel):
    """A tenant and namespace supplied by an authenticated host application."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: Identifier
    namespace: Identifier = "default"


class ArtifactScope(TenantScope):
    """Applicability and visibility of an artifact within a tenant."""

    visibility: ScopeVisibility = ScopeVisibility.AGENT
    agent_id: Identifier | None = None
    agent_type: Identifier | None = None
    task_type: Identifier | None = None
    tool: Identifier | None = None
    workflow: Identifier | None = None

    @model_validator(mode="after")
    def _validate_visibility(self) -> ArtifactScope:
        if self.visibility is ScopeVisibility.AGENT and self.agent_id is None:
            raise ValueError("agent visibility requires agent_id")
        if self.visibility is ScopeVisibility.AGENT_TYPE and self.agent_type is None:
            raise ValueError("agent_type visibility requires agent_type")
        if self.visibility is not ScopeVisibility.AGENT and self.agent_id is not None:
            raise ValueError("shared visibility must not name a private agent")
        if self.visibility is ScopeVisibility.TENANT and self.agent_type is not None:
            raise ValueError("tenant visibility must not name an agent type")
        return self


class Evidence(BaseModel):
    """A scoped reference to supporting or contradictory execution evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    evidence_id: UUID = Field(default_factory=uuid4)
    feedback_id: UUID | None = None
    execution_id: Identifier | None = None
    source: Identifier
    independence_key: Identifier
    quality: float = Field(default=1.0, ge=0.0, le=1.0)
    source_reliability: float = Field(default=1.0, ge=0.0, le=1.0)
    human_reviewed: bool = False
    contradicts: bool = False
    valid: bool = True
    observed_at: AwareDatetime = Field(default_factory=_now)
    provenance: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_reference(self) -> Evidence:
        if self.feedback_id is None and self.execution_id is None:
            raise ValueError("evidence requires a feedback_id or execution_id")
        return self


class ValidationRecord(BaseModel):
    """One reviewer decision, kept independently of feedback approval."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reviewer: Identifier
    approved: bool
    reason: str = ""
    policy_version: Identifier
    reviewed_version: int = Field(ge=1)
    artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    occurred_at: AwareDatetime = Field(default_factory=_now)


class LearningSignal(BaseModel):
    """A normalized, scoped observation extracted from feedback or an outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    signal_id: UUID = Field(default_factory=uuid4)
    kind: SignalType
    scope: ArtifactScope
    content: str = Field(min_length=1, max_length=10000)
    evidence: Evidence
    occurred_at: AwareDatetime = Field(default_factory=_now)

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("signal content must not be blank")
        return value


class ExecutionOutcome(BaseModel):
    """A host-reported outcome associated with one scoped execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: Identifier
    scope: ArtifactScope
    success: bool
    description: str = Field(min_length=1, max_length=10000)
    source: Identifier = "agent"
    occurred_at: AwareDatetime = Field(default_factory=_now)

    @field_validator("description")
    @classmethod
    def _description_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("outcome description must not be blank")
        return value


class LearnedArtifact(BaseModel):
    """One immutable snapshot of a versioned learned artifact.

    The structured artifact repository is authoritative. Vector and graph
    records are projections and must not be used as the artifact source.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    artifact_id: UUID = Field(default_factory=uuid4)
    artifact_type: ArtifactType
    title: str = Field(min_length=1, max_length=512)
    description: str = ""
    content: str = Field(min_length=1, max_length=10000)
    scope: ArtifactScope
    evidence: tuple[Evidence, ...] = ()
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    created_at: AwareDatetime = Field(default_factory=_now)
    updated_at: AwareDatetime = Field(default_factory=_now)
    version: int = Field(default=1, ge=1)
    status: ArtifactStatus = ArtifactStatus.CANDIDATE
    provenance: JsonObject = Field(default_factory=dict)
    embedding_space: EmbeddingSpace | None = None
    validation_history: tuple[ValidationRecord, ...] = ()
    supersedes: UUID | None = None

    @field_validator("title", "content")
    @classmethod
    def _text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("artifact title and content must not be blank")
        return value

    def review_digest(self) -> str:
        """Hash the artifact's reviewable content, scope, evidence, and provenance."""
        body = self.model_dump(
            mode="json",
            exclude={
                "status",
                "version",
                "created_at",
                "updated_at",
                "validation_history",
                "embedding_space",
            },
        )
        encoded = json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @model_validator(mode="before")
    @classmethod
    def _validate_serialized_summary(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = value.copy()
        names = (
            "source_feedback_ids",
            "supporting_execution_ids",
            "evidence_count",
            "contradictory_evidence_count",
        )
        supplied = {name: data.pop(name) for name in names if name in data}
        if not supplied:
            return data
        evidence = tuple(Evidence.model_validate(item) for item in data.get("evidence", ()))
        expected = {
            "source_feedback_ids": tuple(
                dict.fromkeys(str(item.feedback_id) for item in evidence if item.feedback_id)
            ),
            "supporting_execution_ids": tuple(
                dict.fromkeys(
                    item.execution_id
                    for item in evidence
                    if item.execution_id is not None and item.valid and not item.contradicts
                )
            ),
            "evidence_count": sum(item.valid and not item.contradicts for item in evidence),
            "contradictory_evidence_count": sum(
                item.valid and item.contradicts for item in evidence
            ),
        }
        for name, actual in supplied.items():
            if name in ("source_feedback_ids", "supporting_execution_ids"):
                if not isinstance(actual, (tuple, list)):
                    raise ValueError(f"{name} must match the evidence")
                actual = tuple(str(item) for item in actual)
            elif isinstance(actual, bool) or not isinstance(actual, int):
                raise ValueError(f"{name} must match the evidence")
            if actual != expected[name]:
                raise ValueError(f"{name} must match the evidence")
        return data

    @computed_field
    @property
    def source_feedback_ids(self) -> tuple[UUID, ...]:
        """Return distinct source feedback IDs in evidence order."""
        return tuple(dict.fromkeys(item.feedback_id for item in self.evidence if item.feedback_id))

    @computed_field
    @property
    def supporting_execution_ids(self) -> tuple[str, ...]:
        """Return distinct supporting execution IDs in evidence order."""
        return tuple(
            dict.fromkeys(
                item.execution_id
                for item in self.evidence
                if item.execution_id is not None and item.valid and not item.contradicts
            )
        )

    @computed_field
    @property
    def evidence_count(self) -> int:
        """Return the number of supporting evidence records."""
        return sum(item.valid and not item.contradicts for item in self.evidence)

    @computed_field
    @property
    def contradictory_evidence_count(self) -> int:
        """Return the number of contradictory evidence records."""
        return sum(item.valid and item.contradicts for item in self.evidence)

    @model_validator(mode="after")
    def _validate_times(self) -> LearnedArtifact:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        if len({item.evidence_id for item in self.evidence}) != len(self.evidence):
            raise ValueError("evidence IDs must be unique")
        return self


__all__ = [
    "ArtifactScope",
    "ArtifactStatus",
    "ArtifactType",
    "Evidence",
    "ExecutionOutcome",
    "LearnedArtifact",
    "LearningSignal",
    "ScopeVisibility",
    "SignalType",
    "TenantScope",
    "ValidationRecord",
]
