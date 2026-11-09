"""Scope matching for tenant, namespace, agent and task applicability."""

from __future__ import annotations

from feedback_manager.learning.models import ArtifactScope, ScopeVisibility, TenantScope


def is_applicable(
    artifact: ArtifactScope,
    requester: TenantScope,
    *,
    agent_id: str | None = None,
    agent_type: str | None = None,
    task_type: str | None = None,
    tool: str | None = None,
    workflow: str | None = None,
) -> bool:
    """Return whether a scoped artifact is visible and applicable to a requester."""
    if artifact.tenant_id != requester.tenant_id or artifact.namespace != requester.namespace:
        return False
    if artifact.visibility is ScopeVisibility.AGENT and artifact.agent_id != agent_id:
        return False
    if artifact.visibility is ScopeVisibility.AGENT_TYPE and artifact.agent_type != agent_type:
        return False
    return all(
        required is None or required == actual
        for required, actual in (
            (artifact.agent_type, agent_type),
            (artifact.task_type, task_type),
            (artifact.tool, tool),
            (artifact.workflow, workflow),
        )
    )


__all__ = ["is_applicable"]
