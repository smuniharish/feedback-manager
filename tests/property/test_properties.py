"""Property-based tests: invariants that must hold for every input."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from pydantic import ValidationError

from feedback_manager import (
    ExecutionContext,
    FeedbackCategory,
    FeedbackEvent,
    FeedbackLifecycleError,
    FeedbackManager,
    FeedbackManagerError,
    FeedbackQuery,
    FeedbackSource,
    FeedbackStatus,
    FeedbackTarget,
    FeedbackTargetType,
    FeedbackValidationError,
    validate_transition,
)
from feedback_manager.contracts import FeedbackHandler, FeedbackHandlerResult
from feedback_manager.core import LEGAL_TRANSITIONS, is_legal_transition
from feedback_manager.correlation import default_correlation_id
from feedback_manager.integrations.langgraph import execution_context_from_config
from feedback_manager.observability import NoOpObservabilitySink
from feedback_manager.policies import FailureMode, FailurePolicy, FeedbackStage, RetentionPolicy
from feedback_manager.routing import DefaultFeedbackRouter, RoutingRule
from feedback_manager.storage import InMemoryFeedbackStore
from tests import strategies as fs

if TYPE_CHECKING:
    from datetime import datetime, timedelta
    from uuid import UUID


@given(
    value=fs.identifiers,
    value_type=st.sampled_from([FeedbackSource, FeedbackCategory, FeedbackTargetType]),
)
def test_open_values_accept_every_identifier_and_stay_equal(
    value: str, value_type: type[FeedbackSource | FeedbackCategory | FeedbackTargetType]
) -> None:
    wrapped = value_type(value)

    assert wrapped == value
    assert hash(wrapped) == hash(value)
    assert type(value_type(wrapped)) is value_type


@given(value=fs.invalid_identifiers)
def test_open_values_and_identifier_fields_reject_blank_or_padded_strings(value: str) -> None:
    with pytest.raises(FeedbackValidationError):
        FeedbackSource(value)
    with pytest.raises(ValidationError):
        FeedbackTarget(type="node", id=value)


@given(event=fs.events)
def test_events_round_trip_through_json(event: FeedbackEvent) -> None:
    assert FeedbackEvent.model_validate_json(event.model_dump_json()) == event


@given(number=st.sampled_from([float("inf"), float("-inf"), float("nan")]))
async def test_non_finite_numbers_are_rejected_because_json_cannot_hold_them(
    number: float,
) -> None:
    manager = FeedbackManager(observability_sink=NoOpObservabilitySink())
    target = FeedbackTarget(type="run", id="r")

    with pytest.raises(FeedbackValidationError):
        await manager.submit(
            source="human", category="rating", target=target, payload={"x": [number]}
        )
    event = await manager.submit(source="human", category="rating", target=target)
    with pytest.raises(FeedbackValidationError):
        await manager.reject(event.feedback_id, resolution={"score": number})


@given(current=fs.statuses, target=fs.statuses, feedback_id=st.uuids())
def test_validation_agrees_with_the_transition_table(
    current: FeedbackStatus, target: FeedbackStatus, feedback_id: UUID
) -> None:
    legal = current == target or target in LEGAL_TRANSITIONS[current]

    assert is_legal_transition(current, target) is legal
    try:
        transition = validate_transition(feedback_id, current, target)
    except FeedbackLifecycleError:
        assert not legal
    else:
        assert legal
        assert transition.idempotent is (current == target)


KEYS = st.sampled_from(["key-a", "key-b", "key-c"])
"""A few idempotency keys, so generated events and queries share them."""


@given(
    events=st.lists(
        st.builds(
            lambda event, key: event.model_copy(update={"idempotency_key": key}),
            fs.events,
            st.none() | KEYS,
        ),
        max_size=12,
        unique_by=lambda event: event.feedback_id,
    ),
    query=st.builds(
        FeedbackQuery,
        source=st.none() | fs.sources,
        category=st.none() | fs.categories,
        status=st.none() | fs.statuses,
        idempotency_key=st.none() | KEYS,
        newest_first=st.booleans(),
        limit=st.none() | st.integers(min_value=1, max_value=5),
    ),
)
async def test_store_queries_match_a_reference_filter(
    events: list[FeedbackEvent], query: FeedbackQuery
) -> None:
    store = InMemoryFeedbackStore()
    kept: list[FeedbackEvent] = []
    for event in events:
        # The store keeps the first event of each idempotency key.
        key = event.idempotency_key
        if key is None or all(other.idempotency_key != key for other in kept):
            kept.append(event)
        await store.create(event)
    ordered = list(reversed(kept)) if query.newest_first else kept
    expected = [event for event in ordered if query.matches(event)][: query.limit]

    assert list(await store.query(query)) == expected


@given(
    created_at=st.lists(fs.aware_datetimes, min_size=1, max_size=8),
    bounds=st.tuples(fs.aware_datetimes, fs.aware_datetimes),
)
def test_time_range_filters_are_half_open(
    created_at: list[datetime], bounds: tuple[datetime, datetime]
) -> None:
    after, before = sorted(bounds)
    assume(after < before)
    query = FeedbackQuery(created_after=after, created_before=before)

    for moment in created_at:
        event = FeedbackEvent(
            source="human",
            category="rating",
            target=FeedbackTarget(type="run", id="r"),
            created_at=moment,
        )
        assert query.matches(event) is (after <= moment < before)


@given(submissions=st.integers(min_value=1, max_value=8), key=fs.identifiers)
async def test_retries_with_one_idempotency_key_store_one_event(submissions: int, key: str) -> None:
    manager = FeedbackManager(observability_sink=NoOpObservabilitySink())
    target = FeedbackTarget(type="run", id="r")

    results = await asyncio.gather(
        *(
            manager.submit(source="human", category="rating", target=target, idempotency_key=key)
            for _ in range(submissions)
        )
    )

    assert len({event.feedback_id for event in results}) == 1
    assert len(await manager.query()) == 1


@given(context=st.none() | fs.execution_contexts, feedback_id=st.uuids())
def test_correlation_prefers_run_then_thread_then_checkpoint(
    context: ExecutionContext | None, feedback_id: UUID
) -> None:
    event = FeedbackEvent(
        feedback_id=feedback_id,
        source="human",
        category="rating",
        target=FeedbackTarget(type="run", id="r"),
        execution_context=context,
    )
    candidates = (
        [] if context is None else [context.run_id, context.thread_id, context.checkpoint_id]
    )
    expected = next((value for value in candidates if value is not None), str(feedback_id))

    assert default_correlation_id(event) == expected


identifier_like = st.one_of(
    st.none(),
    fs.identifiers,
    fs.identifiers.map(lambda value: f"  {value}  "),
    st.uuids(),
    st.integers(),
    st.just("   "),
)


def _normalized(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip() or None


@given(
    thread=identifier_like, checkpoint=identifier_like, run=identifier_like, node=identifier_like
)
def test_config_identifiers_are_normalized(
    thread: Any, checkpoint: Any, run: Any, node: Any
) -> None:
    context = execution_context_from_config(
        {
            "configurable": {"thread_id": thread, "checkpoint_id": checkpoint},
            "metadata": {"xai_run_id": run, "langgraph_node": node},
        }
    )

    assert context.thread_id == _normalized(thread)
    assert context.checkpoint_id == _normalized(checkpoint)
    assert context.run_id == _normalized(run)
    assert context.node_id == _normalized(node)


class NamedHandler(FeedbackHandler):
    def __init__(self, index: int) -> None:
        self.index = index

    async def handle(self, feedback: FeedbackEvent) -> FeedbackHandlerResult:
        return FeedbackHandlerResult(handled=True)


HANDLERS = [NamedHandler(index) for index in range(4)]


@given(
    rules=st.lists(
        st.tuples(st.booleans(), st.lists(st.sampled_from(HANDLERS), max_size=4)), max_size=5
    ),
    defaults=st.lists(st.sampled_from(HANDLERS), max_size=2),
)
async def test_routing_collects_matching_handlers_in_order_without_duplicates(
    rules: list[tuple[bool, list[NamedHandler]]], defaults: list[NamedHandler]
) -> None:
    router = DefaultFeedbackRouter(
        [
            RoutingRule(predicate=lambda _, matches=matches: matches, handlers=handlers)
            for matches, handlers in rules
        ],
        default_handlers=defaults,
    )
    expected: list[NamedHandler] = []
    for matches, handlers in rules:
        if matches:
            expected.extend(handler for handler in handlers if handler not in expected)
    event = FeedbackEvent(
        source="human", category="rating", target=FeedbackTarget(type="run", id="r")
    )

    assert list(await router.route(event)) == (expected if expected else defaults)


@given(status=fs.statuses, age=fs.durations, elapsed=fs.durations, now=fs.aware_datetimes)
def test_retention_expires_exactly_the_overdue_expirable_events(
    status: FeedbackStatus, age: timedelta, elapsed: timedelta, now: datetime
) -> None:
    event = FeedbackEvent(
        source="human",
        category="rating",
        target=FeedbackTarget(type="run", id="r"),
        status=status,
        created_at=now - elapsed,
    )
    expirable = status in {
        FeedbackStatus.CREATED,
        FeedbackStatus.RECEIVED,
        FeedbackStatus.ACKNOWLEDGED,
    }

    assert RetentionPolicy(max_pending_age=age).is_expired(event, now=now) is (
        expirable and elapsed > age
    )


@given(stage=st.sampled_from(FeedbackStage), mode=st.sampled_from(FailureMode), fails=st.booleans())
async def test_failure_modes(stage: FeedbackStage, mode: FailureMode, fails: bool) -> None:
    policy = FailurePolicy(modes={stage: mode})

    async def operation() -> str:
        if fails:
            raise RuntimeError("stage failed")
        return "done"

    if fails and mode is FailureMode.BLOCKING:
        with pytest.raises(FeedbackManagerError) as raised:
            await policy.run_stage(stage, operation)
        assert isinstance(raised.value.__cause__, RuntimeError)
    else:
        assert await policy.run_stage(stage, operation) == (None if fails else "done")
