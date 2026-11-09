from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from feedback_manager.embeddings import EmbeddingSpace
from feedback_manager.learning import (
    ArtifactScope,
    ArtifactType,
    Evidence,
    ExecutionOutcome,
    LearnedArtifact,
    LearningSignal,
    ScopeVisibility,
    SignalType,
    TenantScope,
)


def test_artifact_round_trip_preserves_evidence_and_summary() -> None:
    feedback_id = uuid4()
    evidence = (
        Evidence(
            feedback_id=feedback_id,
            execution_id="run-1",
            source="human",
            independence_key="reviewer-1",
        ),
        Evidence(
            feedback_id=feedback_id,
            execution_id="run-2",
            source="human",
            independence_key="reviewer-2",
            contradicts=True,
        ),
    )
    artifact = LearnedArtifact(
        artifact_type=ArtifactType.KNOWLEDGE,
        title="Correction",
        content="A corrected fact",
        scope=ArtifactScope(tenant_id="tenant", agent_id="agent"),
        evidence=evidence,
    )

    reconstructed = LearnedArtifact.model_validate_json(artifact.model_dump_json())
    assert reconstructed == artifact
    assert reconstructed.source_feedback_ids == (feedback_id,)
    assert reconstructed.supporting_execution_ids == ("run-1",)
    assert reconstructed.evidence_count == 1
    assert reconstructed.contradictory_evidence_count == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_feedback_ids", ["00000000-0000-0000-0000-000000000000"]),
        ("supporting_execution_ids", ["other"]),
        ("evidence_count", 3),
        ("contradictory_evidence_count", True),
        ("source_feedback_ids", "invalid"),
    ],
)
def test_artifact_rejects_forged_evidence_summary(field: str, value: object) -> None:
    artifact = LearnedArtifact(
        artifact_type=ArtifactType.PATTERN,
        title="Pattern",
        content="Content",
        scope=ArtifactScope(tenant_id="tenant", agent_id="agent"),
    )
    data = artifact.model_dump()
    data[field] = value
    with pytest.raises(ValidationError, match="must match the evidence"):
        LearnedArtifact.model_validate(data)


def test_artifact_rejects_duplicate_evidence_and_inverted_timestamps() -> None:
    evidence = Evidence(feedback_id=uuid4(), source="human", independence_key="human-1")
    scope = ArtifactScope(tenant_id="tenant", agent_id="agent")
    with pytest.raises(ValidationError, match="evidence IDs must be unique"):
        LearnedArtifact(
            artifact_type=ArtifactType.KNOWLEDGE,
            title="Title",
            content="Content",
            scope=scope,
            evidence=(evidence, evidence),
        )
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="updated_at must not precede"):
        LearnedArtifact(
            artifact_type=ArtifactType.KNOWLEDGE,
            title="Title",
            content="Content",
            scope=scope,
            created_at=now,
            updated_at=now - timedelta(seconds=1),
        )


def test_scope_and_evidence_require_trustworthy_identifiers() -> None:
    with pytest.raises(ValidationError, match="agent visibility requires agent_id"):
        ArtifactScope(tenant_id="tenant")
    with pytest.raises(ValidationError, match="agent_type visibility requires agent_type"):
        ArtifactScope(tenant_id="tenant", visibility=ScopeVisibility.AGENT_TYPE)
    with pytest.raises(ValidationError, match="feedback_id or execution_id"):
        Evidence(source="human", independence_key="human-1")
    with pytest.raises(ValidationError):
        TenantScope(tenant_id=" ")
    with pytest.raises(ValidationError, match="shared visibility"):
        ArtifactScope(tenant_id="tenant", visibility=ScopeVisibility.TENANT, agent_id="agent")
    with pytest.raises(ValidationError, match="tenant visibility"):
        ArtifactScope(tenant_id="tenant", visibility=ScopeVisibility.TENANT, agent_type="type")

    tenant_scope = ArtifactScope(tenant_id="tenant", visibility=ScopeVisibility.TENANT)
    assert tenant_scope.namespace == "default"
    assert (
        ArtifactScope(
            tenant_id="tenant", visibility=ScopeVisibility.AGENT_TYPE, agent_type="researcher"
        ).agent_type
        == "researcher"
    )


def test_learning_text_must_not_be_whitespace() -> None:
    scope = ArtifactScope(tenant_id="tenant", agent_id="agent")
    evidence = Evidence(feedback_id=uuid4(), source="human", independence_key="one")
    with pytest.raises(ValidationError, match="signal content must not be blank"):
        LearningSignal(kind=SignalType.CORRECTION, scope=scope, content=" ", evidence=evidence)
    with pytest.raises(ValidationError, match="outcome description must not be blank"):
        ExecutionOutcome(execution_id="one", scope=scope, success=True, description=" ")
    with pytest.raises(ValidationError, match="artifact title and content must not be blank"):
        LearnedArtifact(
            artifact_type=ArtifactType.KNOWLEDGE, title=" ", content="text", scope=scope
        )
    with pytest.raises(ValidationError):
        LearnedArtifact.model_validate("not an artifact")


def test_embedding_space_identity_includes_revision_and_instructions() -> None:
    space = EmbeddingSpace(
        provider="provider", model="model", revision="v1", dimensions=3, query_instruction="query:"
    )
    same = EmbeddingSpace.model_validate_json(space.model_dump_json())
    assert same.space_id == space.space_id
    assert space.model_copy(update={"revision": "v2"}).space_id != space.space_id
    assert space.model_copy(update={"query_instruction": "other:"}).space_id != space.space_id
    with pytest.raises(ValidationError):
        EmbeddingSpace.model_validate(
            {"provider": "provider", "model": "model", "revision": "v1", "dimensions": 0}
        )
