"""The example stores against the `FeedbackStore` contract, including concurrency.

`SQLiteFeedbackStore` always runs. `PostgresFeedbackStore` runs when
``FEEDBACK_MANAGER_TEST_POSTGRES_DSN`` is set; each test then uses its own
table and drops it afterwards.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from feedback_manager import (
    ExecutionContext,
    FeedbackConflictError,
    FeedbackEvent,
    FeedbackLifecycleError,
    FeedbackManager,
    FeedbackNotFoundError,
    FeedbackQuery,
    FeedbackStatus,
    FeedbackStoreError,
    FeedbackTarget,
)
from feedback_manager.storage import InMemoryFeedbackStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from feedback_manager.contracts import FeedbackStore

pytestmark = pytest.mark.examples

DSN = os.environ.get("FEEDBACK_MANAGER_TEST_POSTGRES_DSN", "")
NOW = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture(
    params=[
        "sqlite",
        pytest.param(
            "postgres",
            marks=[
                pytest.mark.postgres,
                pytest.mark.skipif(not DSN, reason="set FEEDBACK_MANAGER_TEST_POSTGRES_DSN"),
            ],
        ),
    ]
)
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[FeedbackStore]:
    if request.param == "sqlite":
        from sqlite_feedback_store import SQLiteFeedbackStore

        yield SQLiteFeedbackStore(tmp_path / "feedback.db")
        return

    pytest.importorskip("psycopg", reason="needs the examples dependency group")
    from postgres_feedback_store import PostgresFeedbackStore
    from psycopg import sql

    table = f"feedback_test_{uuid4().hex[:12]}"
    opened = await PostgresFeedbackStore.open(DSN, table=table, max_connections=20)
    try:
        yield opened
    finally:
        async with opened._pool.connection() as connection:
            await connection.execute(sql.SQL("DROP TABLE {}").format(sql.Identifier(table)))
        await opened.close()


def _event(**overrides: object) -> FeedbackEvent:
    fields: dict[str, object] = {
        "source": "human",
        "category": "correction",
        "target": FeedbackTarget(type="generation", id="gen-1"),
        "status": FeedbackStatus.RECEIVED,
    }
    fields.update(overrides)
    return FeedbackEvent.model_validate(fields)


async def test_create_get_and_duplicates(store: FeedbackStore) -> None:
    event = _event(payload={"nested": {"score": 0.5, "tags": ["a"]}}, correlation_id="run-1")

    assert await store.create(event) == event
    assert await store.get(event.feedback_id) == event
    assert await store.get(uuid4()) is None
    with pytest.raises(FeedbackStoreError, match="already exists"):
        await store.create(event)


async def test_concurrent_idempotent_creates_store_one_event(store: FeedbackStore) -> None:
    results = await asyncio.gather(
        *(store.create(_event(idempotency_key="retry-1")) for _ in range(20))
    )

    assert len({event.feedback_id for event in results}) == 1
    assert len(await store.query(FeedbackQuery())) == 1


async def test_transitions_are_compare_and_set(store: FeedbackStore) -> None:
    event = await store.create(_event())

    acknowledged = await store.transition(
        event.feedback_id, FeedbackStatus.ACKNOWLEDGED, expected=FeedbackStatus.RECEIVED
    )
    assert acknowledged.status is FeedbackStatus.ACKNOWLEDGED
    assert acknowledged.updated_at > event.updated_at
    assert await store.get(event.feedback_id) == acknowledged

    with pytest.raises(FeedbackConflictError):
        await store.transition(
            event.feedback_id, FeedbackStatus.CANCELLED, expected=FeedbackStatus.RECEIVED
        )
    with pytest.raises(FeedbackLifecycleError):
        await store.transition(
            event.feedback_id, FeedbackStatus.RESOLVED, expected=FeedbackStatus.ACKNOWLEDGED
        )
    with pytest.raises(FeedbackNotFoundError):
        await store.transition(uuid4(), FeedbackStatus.CANCELLED, expected=FeedbackStatus.RECEIVED)


async def test_resolutions_are_recorded_once(store: FeedbackStore) -> None:
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

    assert rejected.resolution == repeated.resolution == {"reason": "duplicate"}
    with pytest.raises(FeedbackConflictError):
        await store.transition(
            event.feedback_id, FeedbackStatus.REJECTED, expected=FeedbackStatus.RECEIVED
        )


async def test_queries_match_the_in_memory_reference(store: FeedbackStore) -> None:
    reference = InMemoryFeedbackStore()
    events = [
        _event(
            source=source,
            category=category,
            target=FeedbackTarget(type=target_type, id=f"t-{index % 2}"),
            status=status,
            correlation_id=f"run-{index % 3}",
            execution_context=ExecutionContext(run_id=f"run-{index % 3}"),
            idempotency_key=f"key-{index}" if index % 2 == 0 else None,
            created_at=NOW + timedelta(minutes=index),
        )
        for index, (source, category, target_type, status) in enumerate(
            [
                ("human", "correction", "generation", FeedbackStatus.RECEIVED),
                ("tool", "timeout", "tool_call", FeedbackStatus.ACKNOWLEDGED),
                ("human", "rating", "generation", FeedbackStatus.RESOLVED),
                ("evaluator", "quality", "run", FeedbackStatus.RECEIVED),
                ("tool", "failure", "tool_call", FeedbackStatus.RECEIVED),
            ]
        )
    ]
    for event in events:
        await store.create(event)
        await reference.create(event)
    queries = [
        FeedbackQuery(),
        FeedbackQuery(source="tool"),
        FeedbackQuery(category="rating"),
        FeedbackQuery(target_type="generation", target_id="t-0"),
        FeedbackQuery(status=FeedbackStatus.RECEIVED, newest_first=True),
        FeedbackQuery(correlation_id="run-1"),
        FeedbackQuery(idempotency_key="key-2"),
        FeedbackQuery(idempotency_key="key-1"),
        FeedbackQuery(idempotency_key="key-0", status=FeedbackStatus.ACKNOWLEDGED),
        FeedbackQuery(created_after=NOW + timedelta(minutes=1)),
        FeedbackQuery(created_before=NOW + timedelta(minutes=2), limit=1),
        FeedbackQuery(newest_first=True, limit=2),
    ]

    for query in queries:
        assert await store.query(query) == await reference.query(query), query


async def test_concurrent_lifecycle_calls_publish_each_change_once(store: FeedbackStore) -> None:
    manager = FeedbackManager(store=store)
    changes: list[FeedbackStatus] = []

    async def record(feedback: FeedbackEvent) -> None:
        changes.append(feedback.status)

    event = await manager.submit(
        source="human", category="rating", target=FeedbackTarget(type="run", id="r")
    )
    manager.subscribe(record)

    outcomes = await asyncio.gather(
        *(manager.acknowledge(event.feedback_id) for _ in range(10)),
        *(manager.cancel(event.feedback_id) for _ in range(5)),
        return_exceptions=True,
    )

    final = await manager.get(event.feedback_id)
    assert final is not None
    assert final.status is FeedbackStatus.CANCELLED
    assert all(isinstance(outcome, FeedbackEvent | FeedbackLifecycleError) for outcome in outcomes)
    assert changes in (
        [FeedbackStatus.CANCELLED],
        [FeedbackStatus.ACKNOWLEDGED, FeedbackStatus.CANCELLED],
    )
