"""`FeedbackQuery` validation and matching, and the in-memory store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from feedback_manager import (
    ExecutionContext,
    FeedbackConflictError,
    FeedbackEvent,
    FeedbackLifecycleError,
    FeedbackNotFoundError,
    FeedbackQuery,
    FeedbackStatus,
    FeedbackStoreError,
    FeedbackTarget,
    FeedbackValidationError,
)
from feedback_manager.storage import InMemoryFeedbackStore
from tests.strategies import NAIVE

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def _event(**overrides: object) -> FeedbackEvent:
    fields: dict[str, object] = {
        "source": "human",
        "category": "correction",
        "target": FeedbackTarget(type="generation", id="gen-1"),
        "status": FeedbackStatus.RECEIVED,
    }
    fields.update(overrides)
    return FeedbackEvent.model_validate(fields)


class TestQuery:
    def test_empty_query_matches_everything(self) -> None:
        assert FeedbackQuery().matches(_event())

    def test_status_strings_are_coerced(self) -> None:
        assert FeedbackQuery(status="handled").status is FeedbackStatus.HANDLED  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("arguments", "message"),
        [
            ({"status": "finished"}, "unknown feedback status"),
            ({"created_after": NAIVE}, "timezone-aware"),
            ({"created_before": NAIVE}, "timezone-aware"),
            ({"created_after": NOW, "created_before": NOW}, "earlier than"),
            ({"limit": 0}, "positive integer"),
            ({"limit": True}, "positive integer"),
            ({"limit": 1.5}, "positive integer"),
        ],
    )
    def test_invalid_queries_are_rejected(self, arguments: dict[str, object], message: str) -> None:
        with pytest.raises(FeedbackValidationError, match=message):
            FeedbackQuery(**arguments)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            (FeedbackQuery(source="human"), True),
            (FeedbackQuery(source="tool"), False),
            (FeedbackQuery(category="rating"), False),
            (FeedbackQuery(target_type="tool_call"), False),
            (FeedbackQuery(target_id="gen-2"), False),
            (FeedbackQuery(status=FeedbackStatus.HANDLED), False),
            (FeedbackQuery(correlation_id="run-1"), True),
            (FeedbackQuery(correlation_id="run-2"), False),
            (FeedbackQuery(idempotency_key="key-1"), True),
            (FeedbackQuery(idempotency_key="key-2"), False),
            (FeedbackQuery(created_after=NOW), True),
            (FeedbackQuery(created_after=NOW + timedelta(seconds=1)), False),
            (FeedbackQuery(created_before=NOW), False),
            (FeedbackQuery(created_before=NOW + timedelta(seconds=1)), True),
        ],
    )
    def test_each_filter(self, query: FeedbackQuery, expected: bool) -> None:
        event = _event(correlation_id="run-1", idempotency_key="key-1", created_at=NOW)
        assert query.matches(event) is expected


class TestInMemoryStore:
    async def test_create_and_get(self) -> None:
        store = InMemoryFeedbackStore()
        event = _event()

        assert await store.create(event) is event
        assert await store.get(event.feedback_id) is event
        assert await store.get(uuid4()) is None

    async def test_duplicate_ids_are_rejected(self) -> None:
        store = InMemoryFeedbackStore()
        event = _event()
        await store.create(event)

        with pytest.raises(FeedbackStoreError, match="already exists"):
            await store.create(event)

    async def test_idempotency_key_returns_the_original(self) -> None:
        store = InMemoryFeedbackStore()
        first = await store.create(_event(idempotency_key="key-1"))

        assert await store.create(_event(idempotency_key="key-1")) is first
        assert len(await store.query(FeedbackQuery())) == 1

    async def test_queries_by_idempotency_key_use_the_index(self) -> None:
        store = InMemoryFeedbackStore()
        keyed = await store.create(_event(idempotency_key="key-1"))
        await store.create(_event())

        assert await store.query(FeedbackQuery(idempotency_key="key-1")) == [keyed]
        assert await store.query(FeedbackQuery(idempotency_key="key-2")) == []
        assert (
            await store.query(FeedbackQuery(idempotency_key="key-1", status=FeedbackStatus.HANDLED))
            == []
        )

    async def test_transition_is_a_compare_and_set(self) -> None:
        store = InMemoryFeedbackStore()
        event = await store.create(_event())

        acknowledged = await store.transition(
            event.feedback_id, FeedbackStatus.ACKNOWLEDGED, expected=FeedbackStatus.RECEIVED
        )
        assert acknowledged.status is FeedbackStatus.ACKNOWLEDGED
        assert await store.get(event.feedback_id) is acknowledged

        with pytest.raises(FeedbackConflictError) as raised:
            await store.transition(
                event.feedback_id, FeedbackStatus.CANCELLED, expected=FeedbackStatus.RECEIVED
            )
        assert raised.value.current_status == "acknowledged"

    async def test_transition_records_resolution_and_same_status_is_a_no_op(self) -> None:
        store = InMemoryFeedbackStore()
        event = await store.create(_event())

        rejected = await store.transition(
            event.feedback_id,
            FeedbackStatus.REJECTED,
            expected=FeedbackStatus.RECEIVED,
            resolution={"reason": "duplicate"},
        )
        repeated = await store.transition(
            event.feedback_id,
            FeedbackStatus.REJECTED,
            expected=FeedbackStatus.REJECTED,
            resolution={"reason": "other"},
        )

        assert repeated is rejected
        assert repeated.resolution == {"reason": "duplicate"}

    async def test_illegal_or_unknown_transitions_fail(self) -> None:
        store = InMemoryFeedbackStore()
        event = await store.create(_event())

        with pytest.raises(FeedbackLifecycleError):
            await store.transition(
                event.feedback_id, FeedbackStatus.RESOLVED, expected=FeedbackStatus.RECEIVED
            )
        with pytest.raises(FeedbackNotFoundError):
            await store.transition(
                uuid4(), FeedbackStatus.ACKNOWLEDGED, expected=FeedbackStatus.RECEIVED
            )

    async def test_query_order_and_limit(self) -> None:
        store = InMemoryFeedbackStore()
        created = [
            await store.create(_event(execution_context=ExecutionContext(run_id=f"run-{index}")))
            for index in range(5)
        ]
        ids = [event.feedback_id for event in created]

        oldest = await store.query(FeedbackQuery(limit=2))
        newest = await store.query(FeedbackQuery(newest_first=True, limit=2))
        everything = await store.query(FeedbackQuery(newest_first=True))
        nothing = await store.query(FeedbackQuery(source="tool"))

        assert [event.feedback_id for event in oldest] == ids[:2]
        assert [event.feedback_id for event in newest] == ids[::-1][:2]
        assert [event.feedback_id for event in everything] == ids[::-1]
        assert nothing == []

    async def test_updates_keep_creation_order(self) -> None:
        store = InMemoryFeedbackStore()
        first = await store.create(_event())
        second = await store.create(_event())
        await store.transition(
            first.feedback_id, FeedbackStatus.ACKNOWLEDGED, expected=FeedbackStatus.RECEIVED
        )

        listed = await store.query(FeedbackQuery())

        assert [event.feedback_id for event in listed] == [first.feedback_id, second.feedback_id]
