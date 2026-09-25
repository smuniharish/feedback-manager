"""Unit tests for :class:`InMemoryFeedbackStore`."""

from __future__ import annotations

from uuid import uuid4

import pytest

from feedback_manager.contracts.store import FeedbackQuery
from feedback_manager.core import (
    FeedbackCategory,
    FeedbackEvent,
    FeedbackSource,
    FeedbackStatus,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.errors import FeedbackNotFoundError, FeedbackStoreError
from feedback_manager.storage.memory import InMemoryFeedbackStore


def _event(**overrides: object) -> FeedbackEvent:
    defaults: dict[str, object] = dict(
        source=FeedbackSource.HUMAN,
        category=FeedbackCategory.CORRECTION,
        target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id="gen-1"),
    )
    defaults.update(overrides)
    return FeedbackEvent(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def store() -> InMemoryFeedbackStore:
    return InMemoryFeedbackStore()


async def test_create_and_get(store: InMemoryFeedbackStore) -> None:
    event = _event()
    created = await store.create(event)
    assert created == event
    fetched = await store.get(event.feedback_id)
    assert fetched == event


async def test_get_unknown_returns_none(store: InMemoryFeedbackStore) -> None:
    assert await store.get(uuid4()) is None


async def test_create_duplicate_id_raises(store: InMemoryFeedbackStore) -> None:
    event = _event()
    await store.create(event)
    with pytest.raises(FeedbackStoreError):
        await store.create(event)


async def test_idempotency_key_dedup_returns_original(store: InMemoryFeedbackStore) -> None:
    first = _event(idempotency_key="key-1")
    second = _event(idempotency_key="key-1")
    created_first = await store.create(first)
    created_second = await store.create(second)
    assert created_first.feedback_id == created_second.feedback_id == first.feedback_id


async def test_update_unknown_raises(store: InMemoryFeedbackStore) -> None:
    with pytest.raises(FeedbackNotFoundError):
        await store.update(_event())


async def test_transition_persists_new_status(store: InMemoryFeedbackStore) -> None:
    event = await store.create(_event())
    updated = await store.transition(event.feedback_id, FeedbackStatus.RECEIVED)
    assert updated.status == FeedbackStatus.RECEIVED
    assert (await store.get(event.feedback_id)).status == FeedbackStatus.RECEIVED  # type: ignore[union-attr]


async def test_transition_unknown_raises(store: InMemoryFeedbackStore) -> None:
    with pytest.raises(FeedbackNotFoundError):
        await store.transition(uuid4(), FeedbackStatus.RECEIVED)


async def test_query_filters_by_source_and_category(store: InMemoryFeedbackStore) -> None:
    human = await store.create(_event(source=FeedbackSource.HUMAN))
    tool = await store.create(
        _event(
            source=FeedbackSource.TOOL,
            category=FeedbackCategory.TIMEOUT,
            target=FeedbackTarget(type=FeedbackTargetType.TOOL_CALL, id="call-1"),
        )
    )
    results = await store.query(FeedbackQuery(source="tool"))
    assert [event.feedback_id for event in results] == [tool.feedback_id]
    results = await store.query(FeedbackQuery(source="human"))
    assert [event.feedback_id for event in results] == [human.feedback_id]


async def test_query_limit(store: InMemoryFeedbackStore) -> None:
    for _ in range(5):
        await store.create(_event())
    results = await store.query(FeedbackQuery(limit=2))
    assert len(results) == 2


async def test_list_returns_creation_order(store: InMemoryFeedbackStore) -> None:
    created = [await store.create(_event()) for _ in range(3)]
    listed = await store.list()
    assert [event.feedback_id for event in listed] == [event.feedback_id for event in created]
