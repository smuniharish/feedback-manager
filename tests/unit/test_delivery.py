"""Push delivery: subscriptions and streams."""

from __future__ import annotations

import asyncio
import threading

import pytest

from feedback_manager import (
    FeedbackEvent,
    FeedbackManager,
    FeedbackQuery,
    FeedbackStatus,
    FeedbackTarget,
)
from feedback_manager.errors import FeedbackConfigurationError, FeedbackSubscriberError
from feedback_manager.policies import FailureMode, FailurePolicy, FeedbackStage


async def _submit(
    manager: FeedbackManager, target: FeedbackTarget, **fields: object
) -> FeedbackEvent:
    arguments: dict[str, object] = {"source": "human", "category": "correction", "target": target}
    arguments.update(fields)
    return await manager.submit(**arguments)  # type: ignore[arg-type]


class TestSubscriptions:
    async def test_subscribers_see_new_feedback_and_changes_until_cancelled(
        self, manager: FeedbackManager, target: FeedbackTarget
    ) -> None:
        seen: list[FeedbackStatus] = []

        async def subscriber(feedback: FeedbackEvent) -> None:
            seen.append(feedback.status)

        subscription = manager.subscribe(subscriber)
        event = await _submit(manager, target)
        await manager.acknowledge(event.feedback_id)
        subscription.cancel()
        subscription.cancel()
        await manager.mark_handled(event.feedback_id)

        assert seen == [FeedbackStatus.RECEIVED, FeedbackStatus.ACKNOWLEDGED]
        assert subscription.active is False

    async def test_the_same_callable_can_subscribe_twice(
        self, manager: FeedbackManager, target: FeedbackTarget
    ) -> None:
        seen: list[str] = []

        async def subscriber(feedback: FeedbackEvent) -> None:
            seen.append("called")

        first = manager.subscribe(subscriber)
        with manager.subscribe(subscriber) as second:
            await _submit(manager, target)
            assert second.active
        await _submit(manager, target)
        first.cancel()
        await _submit(manager, target)

        assert seen == ["called", "called", "called"]

    async def test_failing_subscribers_are_isolated(
        self, manager: FeedbackManager, target: FeedbackTarget
    ) -> None:
        seen: list[str] = []

        async def broken(feedback: FeedbackEvent) -> None:
            raise RuntimeError("subscriber exploded")

        async def healthy(feedback: FeedbackEvent) -> None:
            seen.append(str(feedback.status))

        manager.subscribe(broken)
        manager.subscribe(healthy)

        await _submit(manager, target)

        assert seen == ["received"]

    async def test_blocking_subscriber_failures_propagate(self, target: FeedbackTarget) -> None:
        manager = FeedbackManager(
            failure_policy=FailurePolicy(modes={FeedbackStage.SUBSCRIBER: FailureMode.BLOCKING})
        )

        async def broken(feedback: FeedbackEvent) -> None:
            raise RuntimeError("subscriber exploded")

        manager.subscribe(broken)

        with pytest.raises(FeedbackSubscriberError):
            await _submit(manager, target)

    def test_subscribers_must_be_callable(self, manager: FeedbackManager) -> None:
        with pytest.raises(FeedbackConfigurationError):
            manager.subscribe("not callable")  # type: ignore[arg-type]


class TestStreams:
    def test_streams_need_a_running_event_loop(self, manager: FeedbackManager) -> None:
        with pytest.raises(FeedbackConfigurationError, match="running event loop"):
            manager.stream()

    async def test_events_published_after_opening_are_delivered(
        self, manager: FeedbackManager, target: FeedbackTarget
    ) -> None:
        before = await _submit(manager, target)
        stream = manager.stream(FeedbackQuery(category="correction"))
        after = await _submit(manager, target)
        await manager.submit(source="tool", category="timeout", target=target)
        await manager.acknowledge(after.feedback_id)
        await stream.aclose()

        received = [(event.feedback_id, event.status) async for event in stream]

        assert received == [
            (after.feedback_id, FeedbackStatus.RECEIVED),
            (after.feedback_id, FeedbackStatus.ACKNOWLEDGED),
        ]
        assert before.feedback_id not in {feedback_id for feedback_id, _ in received}
        assert stream.closed
        assert [event async for event in stream] == []

    async def test_closed_streams_ignore_new_events(
        self, manager: FeedbackManager, target: FeedbackTarget
    ) -> None:
        async with manager.stream() as stream:
            await _submit(manager, target)
        await _submit(manager, target)
        stream._publish(await _submit(manager, target))
        await stream.aclose()

        assert len([event async for event in stream]) == 1

    async def test_manager_aclose_ends_every_stream(self, target: FeedbackTarget) -> None:
        async with FeedbackManager() as manager:
            first, second = manager.stream(), manager.stream()
            await _submit(manager, target)

        assert [len([e async for e in s]) for s in (first, second)] == [1, 1]
        await _submit(manager, target)
        assert first.closed

    async def test_events_from_other_threads_reach_the_consumer_loop(
        self, manager: FeedbackManager, target: FeedbackTarget
    ) -> None:
        stream = manager.stream()

        def publish_from_another_loop() -> None:
            asyncio.run(_submit(manager, target, payload={"thread": "worker"}))

        await asyncio.to_thread(publish_from_another_loop)
        event = await asyncio.wait_for(anext(stream), timeout=5)

        assert event.payload == {"thread": "worker"}
        await stream.aclose()

    async def test_changes_made_one_after_another_keep_their_order_across_threads(
        self, manager: FeedbackManager, target: FeedbackTarget
    ) -> None:
        stream = manager.stream()
        submitted: list[FeedbackEvent] = []

        def submit_from_another_loop() -> None:
            submitted.append(asyncio.run(_submit(manager, target)))

        # Block this loop while another thread publishes, as a synchronous
        # LangChain call does, then change the event and close before yielding.
        worker = threading.Thread(target=submit_from_another_loop)
        worker.start()
        worker.join()
        await manager.acknowledge(submitted[0].feedback_id)
        await stream.aclose()

        assert [event.status async for event in stream] == [
            FeedbackStatus.RECEIVED,
            FeedbackStatus.ACKNOWLEDGED,
        ]

    async def test_events_published_outside_any_event_loop_are_delivered(
        self, manager: FeedbackManager, target: FeedbackTarget
    ) -> None:
        stream = manager.stream()
        event = await _submit(manager, target)
        assert await anext(stream) == event

        await asyncio.to_thread(stream._publish, event)

        assert await asyncio.wait_for(anext(stream), timeout=5) == event
        await stream.aclose()

    async def test_streams_of_closed_event_loops_are_dropped(
        self, manager: FeedbackManager, target: FeedbackTarget
    ) -> None:
        streams = []

        def open_in_short_lived_loop() -> None:
            async def open_stream() -> None:
                streams.append(manager.stream())

            asyncio.run(open_stream())

        worker = threading.Thread(target=open_in_short_lived_loop)
        worker.start()
        worker.join()
        (orphaned,) = streams

        await _submit(manager, target)
        await manager.aclose()

        assert orphaned.closed
