"""Unit tests for :class:`FeedbackManager`, the public application service."""

from __future__ import annotations

from uuid import uuid4

import pytest

from feedback_manager import (
    FeedbackCategory,
    FeedbackManager,
    FeedbackNotFoundError,
    FeedbackQuery,
    FeedbackSource,
    FeedbackStatus,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.contracts.handler import (
    FeedbackContext,
    FeedbackHandler,
    FeedbackHandlerResult,
)
from feedback_manager.errors import FeedbackLifecycleError


def _target() -> FeedbackTarget:
    return FeedbackTarget(type=FeedbackTargetType.GENERATION, id="gen-1")


async def test_submit_returns_received_event(manager: FeedbackManager) -> None:
    event = await manager.submit(
        source=FeedbackSource.HUMAN, category=FeedbackCategory.CORRECTION, target=_target()
    )
    assert event.status == FeedbackStatus.RECEIVED
    assert await manager.get(event.feedback_id) == event


async def test_submit_accepts_plain_strings_for_source_and_category(
    manager: FeedbackManager,
) -> None:
    event = await manager.submit(source="human", category="correction", target=_target())
    assert event.source == FeedbackSource.HUMAN
    assert event.category == FeedbackCategory.CORRECTION


async def test_full_lifecycle_walk(manager: FeedbackManager) -> None:
    event = await manager.submit(
        source=FeedbackSource.HUMAN, category=FeedbackCategory.CORRECTION, target=_target()
    )
    acknowledged = await manager.acknowledge(event.feedback_id)
    assert acknowledged.status == FeedbackStatus.ACKNOWLEDGED
    handled = await manager.mark_handled(event.feedback_id)
    assert handled.status == FeedbackStatus.HANDLED
    resolved = await manager.resolve(event.feedback_id, resolution={"accepted": True})
    assert resolved.status == FeedbackStatus.RESOLVED
    assert resolved.metadata["resolution"] == {"accepted": True}


async def test_reject_and_cancel_are_terminal(manager: FeedbackManager) -> None:
    event = await manager.submit(
        source=FeedbackSource.HUMAN, category=FeedbackCategory.REJECTION, target=_target()
    )
    rejected = await manager.reject(event.feedback_id, reason="not applicable")
    assert rejected.status == FeedbackStatus.REJECTED
    with pytest.raises(FeedbackLifecycleError):
        await manager.acknowledge(event.feedback_id)


async def test_transition_on_unknown_id_raises_not_found(manager: FeedbackManager) -> None:
    with pytest.raises(FeedbackNotFoundError):
        await manager.acknowledge(uuid4())


async def test_idempotent_submit_returns_same_event(manager: FeedbackManager) -> None:
    first = await manager.submit(
        source=FeedbackSource.HUMAN,
        category=FeedbackCategory.CORRECTION,
        target=_target(),
        idempotency_key="dup-1",
    )
    second = await manager.submit(
        source=FeedbackSource.HUMAN,
        category=FeedbackCategory.CORRECTION,
        target=_target(),
        idempotency_key="dup-1",
    )
    assert first.feedback_id == second.feedback_id


async def test_cancel_and_expire_transitions(manager: FeedbackManager) -> None:
    event = await manager.submit(
        source=FeedbackSource.HUMAN, category=FeedbackCategory.CORRECTION, target=_target()
    )
    cancelled = await manager.cancel(event.feedback_id, reason="user withdrew request")
    assert cancelled.status == FeedbackStatus.CANCELLED
    assert cancelled.metadata["resolution"] == {"reason": "user withdrew request"}

    other = await manager.submit(
        source=FeedbackSource.HUMAN, category=FeedbackCategory.CORRECTION, target=_target()
    )
    expired = await manager.expire(other.feedback_id)
    assert expired.status == FeedbackStatus.EXPIRED


async def test_reject_without_reason_has_no_resolution_metadata(manager: FeedbackManager) -> None:
    event = await manager.submit(
        source=FeedbackSource.HUMAN, category=FeedbackCategory.REJECTION, target=_target()
    )
    rejected = await manager.reject(event.feedback_id)
    assert rejected.status == FeedbackStatus.REJECTED
    assert "resolution" not in rejected.metadata


async def test_query_by_correlation_id(manager: FeedbackManager) -> None:
    from feedback_manager.core import ExecutionContext

    tagged = await manager.submit(
        source=FeedbackSource.HUMAN,
        category=FeedbackCategory.CORRECTION,
        target=_target(),
        execution_context=ExecutionContext(run_id="run-abc"),
    )
    await manager.submit(
        source=FeedbackSource.HUMAN, category=FeedbackCategory.RATING, target=_target()
    )

    results = await manager.query(FeedbackQuery(correlation_id="run-abc"))
    assert [event.feedback_id for event in results] == [tagged.feedback_id]


async def test_query_by_target(manager: FeedbackManager) -> None:
    await manager.submit(
        source=FeedbackSource.HUMAN, category=FeedbackCategory.CORRECTION, target=_target()
    )
    await manager.submit(
        source=FeedbackSource.TOOL,
        category=FeedbackCategory.TIMEOUT,
        target=FeedbackTarget(type=FeedbackTargetType.TOOL_CALL, id="call-1"),
    )
    results = await manager.query(FeedbackQuery(target_type="generation"))
    assert len(results) == 1
    assert results[0].target.id == "gen-1"


async def test_list_returns_all(manager: FeedbackManager) -> None:
    await manager.submit(
        source=FeedbackSource.HUMAN, category=FeedbackCategory.CORRECTION, target=_target()
    )
    await manager.submit(
        source=FeedbackSource.HUMAN, category=FeedbackCategory.RATING, target=_target()
    )
    assert len(await manager.list()) == 2


async def test_subscribe_receives_lifecycle_events(manager: FeedbackManager) -> None:
    received: list[FeedbackStatus] = []

    async def subscriber(event: object) -> None:
        received.append(event.status)  # type: ignore[attr-defined]

    subscription = manager.subscribe(subscriber)
    event = await manager.submit(
        source=FeedbackSource.HUMAN, category=FeedbackCategory.CORRECTION, target=_target()
    )
    await manager.acknowledge(event.feedback_id)
    assert received == [FeedbackStatus.RECEIVED, FeedbackStatus.ACKNOWLEDGED]
    subscription.cancel()
    await manager.mark_handled(event.feedback_id)
    assert received == [FeedbackStatus.RECEIVED, FeedbackStatus.ACKNOWLEDGED]


async def test_stream_yields_matching_events(manager: FeedbackManager) -> None:
    import asyncio

    stream_iter = manager.stream()

    async def producer() -> None:
        await manager.submit(
            source=FeedbackSource.HUMAN, category=FeedbackCategory.CORRECTION, target=_target()
        )

    task = asyncio.create_task(producer())
    event = await asyncio.wait_for(stream_iter.__anext__(), timeout=2)
    await task
    assert event.status == FeedbackStatus.RECEIVED


class _RecordingHandler(FeedbackHandler):
    def __init__(self) -> None:
        self.events: list[object] = []

    async def handle(self, feedback: object, context: FeedbackContext) -> FeedbackHandlerResult:
        self.events.append(feedback)
        return FeedbackHandlerResult(handled=True)


async def test_router_default_handlers_are_invoked() -> None:
    from feedback_manager.routing.default_router import DefaultFeedbackRouter

    handler = _RecordingHandler()
    manager = FeedbackManager(router=DefaultFeedbackRouter(default_handlers=(handler,)))
    await manager.submit(
        source=FeedbackSource.HUMAN, category=FeedbackCategory.CORRECTION, target=_target()
    )
    assert len(handler.events) == 1


async def test_handler_failure_does_not_break_submit() -> None:
    class _BrokenHandler(FeedbackHandler):
        async def handle(self, feedback: object, context: FeedbackContext) -> FeedbackHandlerResult:
            raise RuntimeError("handler exploded")

    from feedback_manager.routing.default_router import DefaultFeedbackRouter

    manager = FeedbackManager(router=DefaultFeedbackRouter(default_handlers=(_BrokenHandler(),)))
    event = await manager.submit(
        source=FeedbackSource.HUMAN, category=FeedbackCategory.CORRECTION, target=_target()
    )
    assert event.status == FeedbackStatus.RECEIVED


async def test_multiple_manager_instances_are_isolated() -> None:
    manager_a = FeedbackManager()
    manager_b = FeedbackManager()
    event = await manager_a.submit(
        source=FeedbackSource.HUMAN, category=FeedbackCategory.CORRECTION, target=_target()
    )
    assert await manager_b.get(event.feedback_id) is None
    assert len(await manager_a.list()) == 1
    assert len(await manager_b.list()) == 0


def test_xai_runtime_and_provenance_adapter_are_mutually_exclusive() -> None:
    from langgraph_xai import XAIRuntime

    from feedback_manager.integrations.xai import XAIProvenanceAdapter

    runtime = XAIRuntime(application_id="app", tenant_id="tenant", graph_id="graph")
    adapter = XAIProvenanceAdapter(runtime)

    with pytest.raises(ValueError, match=r"xai_runtime.*provenance_adapter"):
        FeedbackManager(xai_runtime=runtime, provenance_adapter=adapter)
