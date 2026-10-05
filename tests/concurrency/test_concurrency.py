"""Concurrency: many tasks, threads, and event loops sharing one manager."""

from __future__ import annotations

import asyncio
import sys
import threading
from typing import TYPE_CHECKING, Any

import pytest

from feedback_manager import (
    FeedbackEvent,
    FeedbackLifecycleError,
    FeedbackManager,
    FeedbackStatus,
    FeedbackTarget,
)
from feedback_manager.observability import NoOpObservabilitySink

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterator

pytestmark = pytest.mark.concurrency

TARGET = FeedbackTarget(type="generation", id="gen-1")


@pytest.fixture
def frequent_thread_switches() -> Iterator[None]:
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(previous)


def _in_threads(count: int, work: Callable[[], Coroutine[Any, Any, Any]]) -> list[BaseException]:
    """Run ``work`` concurrently in ``count`` threads, each on its own event loop."""
    errors: list[BaseException] = []
    barrier = threading.Barrier(count)

    def run() -> None:
        barrier.wait()
        try:
            asyncio.run(work())
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=run) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


async def _submit(manager: FeedbackManager, **fields: Any) -> FeedbackEvent:
    return await manager.submit(source="human", category="correction", target=TARGET, **fields)


async def test_concurrent_tasks_all_persist() -> None:
    manager = FeedbackManager(observability_sink=NoOpObservabilitySink())

    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(_submit(manager)) for _ in range(200)]

    assert len({task.result().feedback_id for task in tasks}) == 200
    assert len(await manager.query()) == 200


@pytest.mark.usefixtures("frequent_thread_switches")
async def test_threads_and_event_loops_share_the_in_memory_store_safely() -> None:
    manager = FeedbackManager(observability_sink=NoOpObservabilitySink())

    async def burst() -> None:
        await asyncio.gather(*(_submit(manager) for _ in range(100)))

    errors = _in_threads(12, burst)

    assert errors == []
    assert len(await manager.query()) == 1200


@pytest.mark.usefixtures("frequent_thread_switches")
async def test_concurrent_retries_with_one_key_create_one_event() -> None:
    manager = FeedbackManager(observability_sink=NoOpObservabilitySink())
    seen: list[str] = []

    async def retry() -> None:
        event = await _submit(manager, idempotency_key="request-42")
        seen.append(str(event.feedback_id))

    errors = _in_threads(10, retry)

    assert errors == []
    assert len(set(seen)) == 1
    assert len(await manager.query()) == 1


@pytest.mark.usefixtures("frequent_thread_switches")
async def test_identical_concurrent_transitions_publish_exactly_once() -> None:
    manager = FeedbackManager(observability_sink=NoOpObservabilitySink())
    notifications: list[FeedbackStatus] = []

    async def subscriber(feedback: FeedbackEvent) -> None:
        notifications.append(feedback.status)

    event = await _submit(manager)
    manager.subscribe(subscriber)

    errors = _in_threads(10, lambda: manager.acknowledge(event.feedback_id))

    assert errors == []
    assert notifications == [FeedbackStatus.ACKNOWLEDGED]


@pytest.mark.usefixtures("frequent_thread_switches")
async def test_competing_transitions_apply_in_some_serial_order() -> None:
    manager = FeedbackManager(observability_sink=NoOpObservabilitySink())
    notifications: list[FeedbackEvent] = []

    async def subscriber(feedback: FeedbackEvent) -> None:
        notifications.append(feedback)

    event = await _submit(manager)
    manager.subscribe(subscriber)
    calls = iter([manager.cancel, manager.acknowledge] * 5)
    lock = threading.Lock()

    def next_call() -> Coroutine[Any, Any, FeedbackEvent]:
        with lock:
            call = next(calls)
        return call(event.feedback_id)

    errors = _in_threads(10, next_call)

    final = await manager.get(event.feedback_id)
    assert final is not None
    assert final.status is FeedbackStatus.CANCELLED
    assert all(isinstance(error, FeedbackLifecycleError) for error in errors)
    # Each applied change is delivered once. Changes made concurrently can be
    # delivered in either order; `updated_at` restores the order they were applied in.
    applied = sorted(notifications, key=lambda feedback: feedback.updated_at)
    assert [feedback.status for feedback in applied] in (
        [FeedbackStatus.CANCELLED],
        [FeedbackStatus.ACKNOWLEDGED, FeedbackStatus.CANCELLED],
    )
    assert applied[-1] == final


async def test_a_cancelled_stream_consumer_leaves_the_manager_usable() -> None:
    manager = FeedbackManager(observability_sink=NoOpObservabilitySink())
    stream = manager.stream()
    reader = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)

    reader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reader

    event = await _submit(manager)
    assert event.status is FeedbackStatus.RECEIVED
    assert await asyncio.wait_for(anext(stream), timeout=5) == event
    await manager.aclose()
