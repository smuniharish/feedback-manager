"""Tenant-constrained feedback access for authenticated host applications."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from feedback_manager.contracts.store import FeedbackQuery
from feedback_manager.core.context import ExecutionContext
from feedback_manager.errors import (
    FeedbackNotFoundError,
    FeedbackStoreError,
    FeedbackValidationError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from uuid import UUID

    from feedback_manager.api.manager import FeedbackManager
    from feedback_manager.api.stream import FeedbackStream
    from feedback_manager.api.subscription import Subscription
    from feedback_manager.contracts.subscriber import FeedbackSubscriber
    from feedback_manager.core._types import JsonObject
    from feedback_manager.core.events import FeedbackEvent
    from feedback_manager.core.targets import FeedbackTarget


class TenantFeedbackManager:
    """Constrain feedback operations to a host-authenticated tenant.

    This is data isolation, not authentication. Never let an agent choose this
    tenant identifier. The unscoped manager remains available for trusted admins.
    """

    def __init__(self, manager: FeedbackManager, tenant_id: str) -> None:
        if not tenant_id.strip():
            raise FeedbackValidationError("tenant_id must not be blank")
        self.manager = manager
        self.tenant_id = tenant_id

    def _owns(self, event: FeedbackEvent) -> bool:
        return (
            event.execution_context is not None
            and event.execution_context.tenant_id == self.tenant_id
        )

    async def get(self, feedback_id: UUID) -> FeedbackEvent | None:
        """Return owned feedback only, without disclosing foreign IDs."""
        event = await self.manager.get(feedback_id)
        return event if event is not None and self._owns(event) else None

    async def _require(self, feedback_id: UUID) -> None:
        if await self.get(feedback_id) is None:
            raise FeedbackNotFoundError("unknown feedback event", feedback_id=feedback_id)

    async def query(self, query: FeedbackQuery | None = None) -> Sequence[FeedbackEvent]:
        """Query within the tenant, failing explicitly on a non-conforming store."""
        scoped = replace(query or FeedbackQuery(), tenant_id=self.tenant_id, unscoped_only=False)
        results = await self.manager.query(scoped)
        if any(not self._owns(event) for event in results):
            raise FeedbackStoreError("feedback store returned a record from another tenant")
        return results

    async def submit(
        self,
        *,
        source: str,
        category: str,
        target: FeedbackTarget,
        payload: JsonObject | None = None,
        metadata: JsonObject | None = None,
        execution_context: ExecutionContext | None = None,
        idempotency_key: str | None = None,
        feedback_type: str | None = None,
    ) -> FeedbackEvent:
        """Submit with the authenticated tenant; reject conflicting caller context."""
        if execution_context is not None and execution_context.tenant_id not in (
            None,
            self.tenant_id,
        ):
            raise FeedbackValidationError("execution context belongs to another tenant")
        context = (
            ExecutionContext(tenant_id=self.tenant_id)
            if execution_context is None
            else execution_context.model_copy(update={"tenant_id": self.tenant_id})
        )
        return await self.manager.submit(
            source=source,
            category=category,
            target=target,
            payload=payload,
            metadata=metadata,
            execution_context=context,
            idempotency_key=idempotency_key,
            feedback_type=feedback_type,
        )

    async def acknowledge(self, feedback_id: UUID) -> FeedbackEvent:
        """Acknowledge owned feedback."""
        await self._require(feedback_id)
        return await self.manager.acknowledge(feedback_id)

    async def mark_handled(self, feedback_id: UUID) -> FeedbackEvent:
        """Mark owned feedback as handled."""
        await self._require(feedback_id)
        return await self.manager.mark_handled(feedback_id)

    async def resolve(
        self, feedback_id: UUID, *, resolution: JsonObject | None = None
    ) -> FeedbackEvent:
        """Resolve owned feedback."""
        await self._require(feedback_id)
        return await self.manager.resolve(feedback_id, resolution=resolution)

    async def reject(self, feedback_id: UUID, *, reason: str | None = None) -> FeedbackEvent:
        """Reject owned feedback."""
        await self._require(feedback_id)
        return await self.manager.reject(feedback_id, reason=reason)

    async def cancel(self, feedback_id: UUID, *, reason: str | None = None) -> FeedbackEvent:
        """Cancel owned feedback."""
        await self._require(feedback_id)
        return await self.manager.cancel(feedback_id, reason=reason)

    async def expire(self, feedback_id: UUID) -> FeedbackEvent:
        """Expire owned feedback."""
        await self._require(feedback_id)
        return await self.manager.expire(feedback_id)

    def stream(self, query: FeedbackQuery | None = None) -> FeedbackStream:
        """Stream only this tenant's feedback and lifecycle changes."""
        return self.manager.stream(
            replace(query or FeedbackQuery(), tenant_id=self.tenant_id, unscoped_only=False)
        )

    def subscribe(
        self,
        subscriber: FeedbackSubscriber | Callable[[FeedbackEvent], Awaitable[None]],
    ) -> Subscription:
        """Subscribe to owned records only; plain ``async def`` functions are accepted."""

        async def scoped(feedback: FeedbackEvent) -> None:
            if self._owns(feedback):
                await subscriber(feedback)

        return self.manager.subscribe(scoped)


__all__ = ["TenantFeedbackManager"]
