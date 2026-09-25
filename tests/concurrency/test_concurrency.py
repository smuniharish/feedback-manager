"""Concurrency tests: FeedbackManager must behave correctly under concurrent use."""

from __future__ import annotations

import asyncio

import pytest

from feedback_manager import (
    FeedbackCategory,
    FeedbackManager,
    FeedbackSource,
    FeedbackStatus,
    FeedbackTarget,
    FeedbackTargetType,
)

pytestmark = pytest.mark.concurrency


def _target(i: int = 0) -> FeedbackTarget:
    return FeedbackTarget(type=FeedbackTargetType.GENERATION, id=f"gen-{i}")


async def test_concurrent_submissions_all_persist(manager: FeedbackManager) -> None:
    async def submit_one(i: int) -> None:
        await manager.submit(
            source=FeedbackSource.HUMAN, category=FeedbackCategory.CORRECTION, target=_target(i)
        )

    async with asyncio.TaskGroup() as tg:
        for i in range(50):
            tg.create_task(submit_one(i))

    assert len(await manager.list()) == 50


async def test_concurrent_duplicate_idempotent_submissions_create_one_event(
    manager: FeedbackManager,
) -> None:
    results: list[object] = []

    async def submit_dup() -> None:
        event = await manager.submit(
            source=FeedbackSource.HUMAN,
            category=FeedbackCategory.CORRECTION,
            target=_target(),
            idempotency_key="shared-key",
        )
        results.append(event.feedback_id)

    async with asyncio.TaskGroup() as tg:
        for _ in range(20):
            tg.create_task(submit_dup())

    assert len(set(results)) == 1
    assert len(await manager.list()) == 1


async def test_concurrent_lifecycle_transitions_do_not_corrupt_state(
    manager: FeedbackManager,
) -> None:
    event = await manager.submit(
        source=FeedbackSource.HUMAN, category=FeedbackCategory.CORRECTION, target=_target()
    )

    successes = 0
    failures = 0

    async def try_ack() -> None:
        nonlocal successes, failures
        try:
            await manager.acknowledge(event.feedback_id)
            successes += 1
        except Exception:
            failures += 1

    async with asyncio.TaskGroup() as tg:
        for _ in range(10):
            tg.create_task(try_ack())

    # All 10 attempts move CREATED-adjacent RECEIVED -> ACKNOWLEDGED; because the
    # transition is idempotent (same-state is always legal), every concurrent
    # acknowledge should succeed without corrupting the final state.
    assert successes == 10
    assert failures == 0
    final = await manager.get(event.feedback_id)
    assert final is not None
    assert final.status == FeedbackStatus.ACKNOWLEDGED


async def test_concurrent_subscribers_all_receive_events(manager: FeedbackManager) -> None:
    counters = [0, 0, 0]

    def make_subscriber(index: int):
        async def _subscriber(event: object) -> None:
            counters[index] += 1

        return _subscriber

    for i in range(3):
        manager.subscribe(make_subscriber(i))

    async def submit_one(i: int) -> None:
        await manager.submit(
            source=FeedbackSource.HUMAN, category=FeedbackCategory.CORRECTION, target=_target(i)
        )

    async with asyncio.TaskGroup() as tg:
        for i in range(10):
            tg.create_task(submit_one(i))

    assert counters == [10, 10, 10]


async def test_shutdown_cancels_active_stream_cleanly(manager: FeedbackManager) -> None:
    stream_iter = manager.stream()
    task = asyncio.ensure_future(stream_iter.__anext__())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The manager must remain usable after a cancelled stream consumer.
    event = await manager.submit(
        source=FeedbackSource.HUMAN, category=FeedbackCategory.CORRECTION, target=_target()
    )
    assert event.status == FeedbackStatus.RECEIVED
