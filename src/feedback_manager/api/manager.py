"""``FeedbackManager``: the small, stable public application service.

This is the single entry point most applications need. It wires together
the extension points (store, router, correlator, provenance adapter,
failure policy, observability sink) via dependency injection -- there is no
hidden global state, and every :class:`FeedbackManager` instance is fully
independent of every other one (Section 29 of the spec).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any
from uuid import UUID

from feedback_manager.api.queries import FeedbackQuery
from feedback_manager.api.subscription import Subscription
from feedback_manager.contracts.correlator import FeedbackCorrelator
from feedback_manager.contracts.handler import FeedbackContext
from feedback_manager.contracts.policy import FeedbackLifecyclePolicy, FeedbackPolicy
from feedback_manager.contracts.provenance import FeedbackProvenanceAdapter
from feedback_manager.contracts.router import FeedbackRouter
from feedback_manager.contracts.store import FeedbackStore
from feedback_manager.contracts.subscriber import FeedbackSubscriber
from feedback_manager.core.categories import FeedbackCategory
from feedback_manager.core.context import ExecutionContext
from feedback_manager.core.events import FeedbackEvent
from feedback_manager.core.sources import FeedbackSource
from feedback_manager.core.status import FeedbackStatus
from feedback_manager.core.targets import FeedbackTarget
from feedback_manager.correlation.correlator import DefaultFeedbackCorrelator
from feedback_manager.errors import FeedbackNotFoundError, FeedbackStoreError
from feedback_manager.observability.hooks import (
    FEEDBACK_ACKNOWLEDGED,
    FEEDBACK_CREATED,
    FEEDBACK_HANDLED,
    FEEDBACK_RECEIVED,
    FEEDBACK_RESOLVED,
    FEEDBACK_ROUTED,
    LoggingObservabilitySink,
    ObservabilityEvent,
    ObservabilitySink,
)
from feedback_manager.policies.failure import FailurePolicy, FeedbackStage
from feedback_manager.routing.default_router import DefaultFeedbackRouter
from feedback_manager.storage.memory import InMemoryFeedbackStore

_STATUS_EVENT_NAMES: dict[FeedbackStatus, str] = {
    FeedbackStatus.ACKNOWLEDGED: FEEDBACK_ACKNOWLEDGED,
    FeedbackStatus.HANDLED: FEEDBACK_HANDLED,
    FeedbackStatus.RESOLVED: FEEDBACK_RESOLVED,
    FeedbackStatus.REJECTED: FEEDBACK_RESOLVED,
    FeedbackStatus.CANCELLED: FEEDBACK_RESOLVED,
    FeedbackStatus.EXPIRED: FEEDBACK_RESOLVED,
}


class FeedbackManager:
    """Capture, correlate, persist, route, and resolve feedback.

    Every dependency is optional and defaults to an in-memory/no-op
    implementation, so ``FeedbackManager()`` is immediately usable; pass in
    your own :class:`~feedback_manager.contracts.store.FeedbackStore`,
    :class:`~feedback_manager.contracts.router.FeedbackRouter`, etc. to
    plug in production infrastructure without modifying this class.
    """

    def __init__(
        self,
        *,
        store: FeedbackStore | None = None,
        router: FeedbackRouter | None = None,
        correlator: FeedbackCorrelator | None = None,
        provenance_adapter: FeedbackProvenanceAdapter | None = None,
        lifecycle_policy: FeedbackLifecyclePolicy | None = None,
        redaction_policy: FeedbackPolicy | None = None,
        failure_policy: FailurePolicy | None = None,
        observability_sink: ObservabilitySink | None = None,
    ) -> None:
        self._store: FeedbackStore = store if store is not None else InMemoryFeedbackStore()
        self._router: FeedbackRouter = router if router is not None else DefaultFeedbackRouter()
        self._correlator: FeedbackCorrelator = (
            correlator if correlator is not None else DefaultFeedbackCorrelator()
        )
        self._provenance_adapter = provenance_adapter
        self._lifecycle_policy = lifecycle_policy
        self._redaction_policy = redaction_policy
        self._failure_policy = failure_policy if failure_policy is not None else FailurePolicy()
        self._observability_sink: ObservabilitySink = (
            observability_sink if observability_sink is not None else LoggingObservabilitySink()
        )
        self._subscribers: list[FeedbackSubscriber] = []
        self._stream_queues: list[asyncio.Queue[FeedbackEvent]] = []

    # -- submission -----------------------------------------------------

    async def submit(
        self,
        *,
        source: FeedbackSource | str,
        category: FeedbackCategory | str,
        target: FeedbackTarget,
        payload: dict[str, Any] | None = None,
        execution_context: ExecutionContext | None = None,
        idempotency_key: str | None = None,
        feedback_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> FeedbackEvent:
        """Submit a new piece of feedback and run it through the pipeline.

        Pipeline: build -> correlate -> resolve provenance (best-effort) ->
        persist -> transition to ``RECEIVED`` -> notify subscribers -> route
        to handlers (best-effort, isolated per handler).
        """
        event = FeedbackEvent(
            source=FeedbackSource(source),
            category=FeedbackCategory(category),
            target=target,
            payload=payload or {},
            execution_context=execution_context,
            idempotency_key=idempotency_key,
            feedback_type=feedback_type,
            metadata=metadata or {},
        )
        if self._redaction_policy is not None:
            event = self._redaction_policy.apply(event)

        correlation = await self._correlator.correlate(event, execution_context)
        event = event.with_correlation(correlation)

        if self._provenance_adapter is not None:
            provenance_adapter = self._provenance_adapter
            provenance = await self._failure_policy.run_stage(
                FeedbackStage.PROVENANCE, lambda: provenance_adapter.resolve(correlation)
            )
            if provenance is not None:
                event = event.with_provenance(provenance)

        created = await self._failure_policy.run_stage(
            FeedbackStage.STORE, lambda: self._store.create(event)
        )
        if created is None:
            raise FeedbackStoreError(
                "failed to persist feedback event", feedback_id=event.feedback_id
            )
        if created.feedback_id != event.feedback_id:
            # Idempotency-key hit: an event with this key already existed and may
            # already be anywhere in its lifecycle. Submission is a no-op that
            # simply returns the current state, per Section 28 of the spec.
            return created
        self._emit(FEEDBACK_CREATED, created)

        received = await self._failure_policy.run_stage(
            FeedbackStage.STORE,
            lambda: self._store.transition(created.feedback_id, FeedbackStatus.RECEIVED),
        )
        if received is None:
            raise FeedbackStoreError(
                "failed to transition feedback to RECEIVED", feedback_id=created.feedback_id
            )
        self._emit(FEEDBACK_RECEIVED, received)
        await self._notify_subscribers(received)
        await self._route(received)
        return received

    async def _route(self, feedback: FeedbackEvent) -> None:
        handlers = await self._failure_policy.run_stage(
            FeedbackStage.ROUTING, lambda: self._router.route(feedback)
        )
        if not handlers:
            return
        context = FeedbackContext(correlation=feedback.correlation)
        for handler in handlers:

            async def _run(handler: Any = handler) -> None:
                await handler.handle(feedback, context)

            await self._failure_policy.run_stage(FeedbackStage.HANDLER, _run)
        self._emit(FEEDBACK_ROUTED, feedback, attributes={"handler_count": len(handlers)})

    # -- lifecycle --------------------------------------------------------

    async def acknowledge(self, feedback_id: UUID) -> FeedbackEvent:
        """Transition feedback to ``ACKNOWLEDGED`` (a consumer has seen it)."""
        return await self._transition(feedback_id, FeedbackStatus.ACKNOWLEDGED)

    async def mark_handled(self, feedback_id: UUID) -> FeedbackEvent:
        """Transition feedback to ``HANDLED`` (processing is complete, pending resolution)."""
        return await self._transition(feedback_id, FeedbackStatus.HANDLED)

    async def resolve(
        self, feedback_id: UUID, *, resolution: dict[str, Any] | None = None
    ) -> FeedbackEvent:
        """Transition feedback to the terminal ``RESOLVED`` state."""
        return await self._transition(feedback_id, FeedbackStatus.RESOLVED, resolution=resolution)

    async def reject(self, feedback_id: UUID, *, reason: str | None = None) -> FeedbackEvent:
        """Transition feedback to the terminal ``REJECTED`` state."""
        return await self._transition(
            feedback_id, FeedbackStatus.REJECTED, resolution={"reason": reason} if reason else None
        )

    async def cancel(self, feedback_id: UUID, *, reason: str | None = None) -> FeedbackEvent:
        """Transition feedback to the terminal ``CANCELLED`` state."""
        return await self._transition(
            feedback_id, FeedbackStatus.CANCELLED, resolution={"reason": reason} if reason else None
        )

    async def expire(self, feedback_id: UUID) -> FeedbackEvent:
        """Transition feedback to the terminal ``EXPIRED`` state."""
        return await self._transition(feedback_id, FeedbackStatus.EXPIRED)

    async def _transition(
        self,
        feedback_id: UUID,
        status: FeedbackStatus,
        *,
        resolution: dict[str, Any] | None = None,
    ) -> FeedbackEvent:
        current = await self._store.get(feedback_id)
        if current is None:
            raise FeedbackNotFoundError("unknown feedback id", feedback_id=feedback_id)
        if self._lifecycle_policy is not None:
            self._lifecycle_policy.authorize_transition(current, status)
        if resolution:
            merged = current.model_copy(
                update={"metadata": {**current.metadata, "resolution": resolution}}
            )
            current = await self._failure_policy.run_stage(
                FeedbackStage.STORE, lambda: self._store.update(merged)
            )
            if current is None:
                raise FeedbackStoreError(
                    "failed to persist resolution metadata", feedback_id=feedback_id
                )

        updated = await self._failure_policy.run_stage(
            FeedbackStage.STORE, lambda: self._store.transition(feedback_id, status)
        )
        if updated is None:
            raise FeedbackStoreError(
                "failed to persist lifecycle transition", feedback_id=feedback_id
            )
        self._emit(_STATUS_EVENT_NAMES.get(status, FEEDBACK_HANDLED), updated)
        await self._notify_subscribers(updated)
        return updated

    # -- retrieval --------------------------------------------------------

    async def get(self, feedback_id: UUID) -> FeedbackEvent | None:
        """Pull-based retrieval of a single feedback event."""
        return await self._store.get(feedback_id)

    async def query(self, query: FeedbackQuery) -> Sequence[FeedbackEvent]:
        """Pull-based retrieval of every feedback event matching ``query``."""
        return await self._store.query(query)

    async def list(self) -> Sequence[FeedbackEvent]:
        """Pull-based retrieval of every stored feedback event."""
        return await self._store.list()

    # -- subscriptions ----------------------------------------------------

    def subscribe(self, subscriber: FeedbackSubscriber) -> Subscription:
        """Register a push-based subscriber, notified on every create/transition."""
        self._subscribers.append(subscriber)

        def _cancel() -> None:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

        return Subscription(_cancel)

    async def stream(self, query: FeedbackQuery | None = None) -> AsyncIterator[FeedbackEvent]:
        """Stream feedback events (optionally filtered) as they occur."""
        queue: asyncio.Queue[FeedbackEvent] = asyncio.Queue()
        self._stream_queues.append(queue)
        try:
            while True:
                event = await queue.get()
                if query is None or query.matches(event):
                    yield event
        finally:
            if queue in self._stream_queues:
                self._stream_queues.remove(queue)

    async def _notify_subscribers(self, event: FeedbackEvent) -> None:
        for subscriber in list(self._subscribers):

            async def _notify(subscriber: FeedbackSubscriber = subscriber) -> None:
                await subscriber(event)

            await self._failure_policy.run_stage(FeedbackStage.SUBSCRIBER, _notify)
        for queue in list(self._stream_queues):
            queue.put_nowait(event)

    # -- observability ----------------------------------------------------

    def _emit(
        self, name: str, event: FeedbackEvent, *, attributes: dict[str, Any] | None = None
    ) -> None:
        self._observability_sink.emit(
            ObservabilityEvent(
                name=name,
                feedback_id=event.feedback_id,
                attributes={
                    "source": str(event.source),
                    "category": str(event.category),
                    "status": str(event.status),
                    **(attributes or {}),
                },
            )
        )


__all__ = ["FeedbackManager"]
