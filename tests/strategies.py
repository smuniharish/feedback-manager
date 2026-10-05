"""Hypothesis strategies for the feedback domain model."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import strategies as st

from feedback_manager import (
    ExecutionContext,
    FeedbackCategory,
    FeedbackEvent,
    FeedbackSource,
    FeedbackStatus,
    FeedbackTarget,
    FeedbackTargetType,
)

identifiers = st.text(
    alphabet=st.characters(codec="utf-8", exclude_categories=("Cs",)), min_size=1, max_size=24
).filter(lambda value: value == value.strip() and bool(value.strip()))
"""Valid identifiers: non-empty, without surrounding whitespace."""

invalid_identifiers = st.one_of(
    st.just(""),
    st.text(alphabet=" \t\n", min_size=1, max_size=4),
    identifiers.map(lambda value: f" {value}"),
    identifiers.map(lambda value: f"{value}\n"),
)
"""Strings an identifier must reject."""

json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=12),
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=8), children, max_size=4)
    ),
    max_leaves=12,
)
json_objects = st.dictionaries(st.text(max_size=8), json_values, max_size=4)

sources = st.sampled_from(FeedbackSource.known_values()) | identifiers.map(FeedbackSource)
categories = st.sampled_from(FeedbackCategory.known_values()) | identifiers.map(FeedbackCategory)
target_types = st.sampled_from(FeedbackTargetType.known_values()) | identifiers.map(
    FeedbackTargetType
)
statuses = st.sampled_from(FeedbackStatus)
targets = st.builds(FeedbackTarget, type=target_types, id=identifiers)

optional_identifiers = st.none() | identifiers
execution_contexts = st.builds(
    ExecutionContext,
    thread_id=optional_identifiers,
    run_id=optional_identifiers,
    checkpoint_id=optional_identifiers,
    node_id=optional_identifiers,
    tool_call_id=optional_identifiers,
)

aware_datetimes = st.datetimes(
    min_value=datetime(2000, 1, 1, tzinfo=UTC).replace(tzinfo=None),
    max_value=datetime(2100, 1, 1, tzinfo=UTC).replace(tzinfo=None),
    timezones=st.just(UTC),
)

NAIVE = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)
"""A naive datetime, for tests that check naive times are rejected."""

events = st.builds(
    FeedbackEvent,
    feedback_id=st.uuids(),
    source=sources,
    category=categories,
    target=targets,
    payload=json_objects,
    execution_context=st.none() | execution_contexts,
    correlation_id=optional_identifiers,
    status=statuses,
    created_at=aware_datetimes,
)

durations = st.timedeltas(min_value=timedelta(seconds=1), max_value=timedelta(days=3650))
