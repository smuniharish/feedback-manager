"""A stateful model of the feedback lifecycle, checked against `FeedbackManager`."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, TypeVar

import pytest
from hypothesis import strategies as st
from hypothesis.stateful import Bundle, RuleBasedStateMachine, invariant, rule

from feedback_manager import (
    FeedbackEvent,
    FeedbackLifecycleError,
    FeedbackManager,
    FeedbackStatus,
    FeedbackTarget,
)
from feedback_manager.core import is_legal_transition
from feedback_manager.observability import NoOpObservabilitySink
from tests import strategies as fs

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from datetime import datetime
    from uuid import UUID

    from feedback_manager.core import JsonObject

T = TypeVar("T")

OPERATIONS: dict[str, FeedbackStatus] = {
    "acknowledge": FeedbackStatus.ACKNOWLEDGED,
    "mark_handled": FeedbackStatus.HANDLED,
    "resolve": FeedbackStatus.RESOLVED,
    "reject": FeedbackStatus.REJECTED,
    "cancel": FeedbackStatus.CANCELLED,
    "expire": FeedbackStatus.EXPIRED,
}


class LifecycleMachine(RuleBasedStateMachine):
    """Random submissions and lifecycle calls must keep the store equal to the model."""

    feedback = Bundle("feedback")

    def __init__(self) -> None:
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.manager = FeedbackManager(observability_sink=NoOpObservabilitySink())
        self.statuses: dict[UUID, FeedbackStatus] = {}
        self.resolutions: dict[UUID, JsonObject | None] = {}
        self.updated_at: dict[UUID, datetime] = {}
        self.notifications = 0
        self.expected_notifications = 0

        async def count(feedback: FeedbackEvent) -> None:
            self.notifications += 1

        self.manager.subscribe(count)

    def run(self, coroutine: Coroutine[Any, Any, T]) -> T:
        return self.loop.run_until_complete(coroutine)

    @rule(target=feedback, source=fs.sources, category=fs.categories)
    def submit(self, source: str, category: str) -> UUID:
        event = self.run(
            self.manager.submit(
                source=source, category=category, target=FeedbackTarget(type="run", id="r")
            )
        )
        self.statuses[event.feedback_id] = FeedbackStatus.RECEIVED
        self.resolutions[event.feedback_id] = None
        self.updated_at[event.feedback_id] = event.updated_at
        self.expected_notifications += 1
        return event.feedback_id

    @rule(
        feedback_id=feedback,
        operation=st.sampled_from(sorted(OPERATIONS)),
        note=st.none() | fs.identifiers,
    )
    def move(self, feedback_id: UUID, operation: str, note: str | None) -> None:
        target = OPERATIONS[operation]
        current = self.statuses[feedback_id]
        arguments: dict[str, Any] = {}
        resolution: JsonObject | None = None
        if operation in {"reject", "cancel"}:
            arguments["reason"] = note
            resolution = None if note is None else {"reason": note}
        elif operation == "resolve" and note is not None:
            resolution = {"note": note}
            arguments["resolution"] = resolution
        call = getattr(self.manager, operation)

        if not is_legal_transition(current, target):
            with pytest.raises(FeedbackLifecycleError):
                self.run(call(feedback_id, **arguments))
            return
        result = self.run(call(feedback_id, **arguments))
        assert result.status is target
        if current is target:
            # A repeated move changes nothing, not even the timestamp.
            assert result.updated_at == self.updated_at[feedback_id]
        else:
            # Every change moves `updated_at` forward, so it orders an event's changes.
            assert result.updated_at > self.updated_at[feedback_id]
            self.updated_at[feedback_id] = result.updated_at
            self.statuses[feedback_id] = target
            self.expected_notifications += 1
            if resolution is not None:
                self.resolutions[feedback_id] = resolution

    @invariant()
    def store_matches_the_model(self) -> None:
        events = self.run(self.manager.query())
        assert {event.feedback_id: event.status for event in events} == self.statuses
        assert {event.feedback_id: event.resolution for event in events} == self.resolutions

    @invariant()
    def every_real_change_is_published_exactly_once(self) -> None:
        assert self.notifications == self.expected_notifications

    def teardown(self) -> None:
        self.loop.close()


TestLifecycleMachine = LifecycleMachine.TestCase
