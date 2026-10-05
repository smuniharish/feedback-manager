"""The domain model: open values, identifiers, models, lifecycle, and errors."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from feedback_manager import (
    ExecutionContext,
    FeedbackCategory,
    FeedbackConflictError,
    FeedbackEvent,
    FeedbackLifecycleError,
    FeedbackManagerError,
    FeedbackNotFoundError,
    FeedbackProvenanceReference,
    FeedbackSource,
    FeedbackStatus,
    FeedbackTarget,
    FeedbackTargetType,
    FeedbackValidationError,
    validate_transition,
)
from feedback_manager.core import LEGAL_TRANSITIONS, TERMINAL_STATUSES, is_legal_transition
from feedback_manager.core import events as events_module
from feedback_manager.errors import (
    FeedbackConfigurationError,
    FeedbackCorrelationError,
    FeedbackHandlerError,
    FeedbackRoutingError,
    FeedbackStoreError,
    FeedbackSubscriberError,
)
from tests.strategies import NAIVE


def _event(**overrides: object) -> FeedbackEvent:
    fields: dict[str, object] = {
        "source": FeedbackSource.HUMAN,
        "category": FeedbackCategory.CORRECTION,
        "target": FeedbackTarget(type=FeedbackTargetType.GENERATION, id="gen-1"),
    }
    fields.update(overrides)
    return FeedbackEvent.model_validate(fields)


class TestOpenValues:
    @pytest.mark.parametrize(
        ("value_type", "count"),
        [(FeedbackSource, 8), (FeedbackCategory, 15), (FeedbackTargetType, 13)],
    )
    def test_known_values_are_distinct_and_in_definition_order(
        self, value_type: type[FeedbackSource], count: int
    ) -> None:
        values = value_type.known_values()

        assert len(values) == len(set(values)) == count
        assert all(isinstance(value, value_type) for value in values)

    def test_known_values_start_with_the_first_constant(self) -> None:
        assert FeedbackSource.known_values()[0] == "human"
        assert FeedbackCategory.known_values()[-1] == "completion"

    def test_values_behave_like_the_strings_they_wrap(self) -> None:
        custom = FeedbackSource("mcp_server")

        assert custom == "mcp_server"
        assert hash(custom) == hash("mcp_server")
        assert isinstance(custom, str)
        assert FeedbackSource(custom) is custom

    @pytest.mark.parametrize("value", ["", "   ", " human", "human\n"])
    def test_blank_or_padded_values_are_rejected(self, value: str) -> None:
        with pytest.raises(FeedbackValidationError, match="FeedbackCategory"):
            FeedbackCategory(value)

    def test_non_strings_are_rejected(self) -> None:
        with pytest.raises(FeedbackValidationError, match="must be a string, not int"):
            FeedbackTargetType(42)  # type: ignore[arg-type]

    def test_validation_error_is_also_a_value_error(self) -> None:
        assert issubclass(FeedbackValidationError, ValueError)

    def test_values_validate_and_serialize_through_pydantic(self) -> None:
        class Model(BaseModel):
            source: FeedbackSource

        model = Model.model_validate({"source": "evaluator"})

        assert type(model.source) is FeedbackSource
        assert model.model_dump(mode="json") == {"source": "evaluator"}
        with pytest.raises(ValidationError):
            Model.model_validate({"source": ""})

    def test_values_survive_copies(self) -> None:
        assert copy.copy(FeedbackSource.HUMAN) == FeedbackSource.HUMAN
        assert type(copy.deepcopy(FeedbackCategory.RATING)) is FeedbackCategory


class TestModels:
    def test_event_defaults(self) -> None:
        event = _event()

        assert event.status is FeedbackStatus.CREATED
        assert event.payload == {} == event.metadata
        assert event.correlation_id is None
        assert event.provenance is None
        assert event.resolution is None
        assert event.created_at.tzinfo is not None

    def test_event_is_frozen(self) -> None:
        with pytest.raises(ValidationError):
            _event().status = FeedbackStatus.RECEIVED  # type: ignore[misc]

    @pytest.mark.parametrize(
        "overrides",
        [
            {"created_at": NAIVE},
            {"payload": {"value": object()}},
            {"idempotency_key": ""},
            {"feedback_type": " padded"},
            {"target": {"type": "generation", "id": ""}},
            {"execution_context": {"thread_id": "  "}},
        ],
    )
    def test_invalid_events_are_rejected(self, overrides: dict[str, object]) -> None:
        with pytest.raises(ValidationError):
            _event(**overrides)

    def test_with_status_returns_an_updated_copy(self) -> None:
        event = _event()

        moved = event.with_status(FeedbackStatus.RECEIVED)
        closed = moved.with_status(FeedbackStatus.REJECTED, resolution={"reason": "duplicate"})

        assert event.status is FeedbackStatus.CREATED
        assert moved.status is FeedbackStatus.RECEIVED
        assert moved.updated_at >= event.updated_at
        assert moved.resolution is None
        assert closed.resolution == {"reason": "duplicate"}
        assert closed.with_status(FeedbackStatus.REJECTED).resolution == {"reason": "duplicate"}

    def test_updated_at_moves_forward_even_when_the_clock_does_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        event = _event()
        # A coarse clock, or one that a skewed host set back, repeats or goes back.
        monkeypatch.setattr(
            events_module, "_utc_now", lambda: event.updated_at - timedelta(seconds=5)
        )

        received = event.with_status(FeedbackStatus.RECEIVED)
        acknowledged = received.with_status(FeedbackStatus.ACKNOWLEDGED)

        tick = timedelta(microseconds=1)
        assert received.updated_at == event.updated_at + tick
        assert acknowledged.updated_at == received.updated_at + tick

    def test_updated_at_follows_the_clock_when_it_advances(self) -> None:
        past = _event(updated_at=datetime(2026, 1, 1, tzinfo=UTC))

        assert past.with_status(FeedbackStatus.RECEIVED).updated_at > datetime(
            2026, 2, 1, tzinfo=UTC
        )

    def test_event_json_round_trip(self) -> None:
        event = _event(
            payload={"score": 0.5, "tags": ["a"], "nested": {"ok": True, "none": None}},
            execution_context=ExecutionContext(run_id="run-1", interrupt_id="i-1"),
            provenance=FeedbackProvenanceReference(
                run_id="run-1", execution_id="exec-1", summary="run", evidence_ids=("e-1",)
            ),
            resolution={"applied": True},
        )

        assert FeedbackEvent.model_validate_json(event.model_dump_json()) == event

    def test_target_and_context_are_frozen(self) -> None:
        with pytest.raises(ValidationError):
            FeedbackTarget(type="node", id="n").id = "other"  # type: ignore[misc]
        with pytest.raises(ValidationError):
            ExecutionContext().run_id = "run"  # type: ignore[misc]


class TestLifecycle:
    def test_every_status_has_a_transition_entry(self) -> None:
        assert set(LEGAL_TRANSITIONS) == set(FeedbackStatus)

    def test_transition_table_is_read_only(self) -> None:
        with pytest.raises(TypeError):
            LEGAL_TRANSITIONS[FeedbackStatus.CREATED] = frozenset()  # type: ignore[index]

    @pytest.mark.parametrize("current", list(FeedbackStatus))
    @pytest.mark.parametrize("target", list(FeedbackStatus))
    def test_legality_matches_the_table(
        self, current: FeedbackStatus, target: FeedbackStatus
    ) -> None:
        expected = current == target or target in LEGAL_TRANSITIONS[current]

        assert is_legal_transition(current, target) is expected
        if expected:
            transition = validate_transition(uuid4(), current, target)
            assert transition.idempotent is (current == target)
            assert (transition.previous_status, transition.new_status) == (current, target)
            assert transition.occurred_at.tzinfo is UTC
        else:
            with pytest.raises(FeedbackLifecycleError) as raised:
                validate_transition(uuid4(), current, target)
            assert raised.value.current_status == current.value
            assert raised.value.requested_status == target.value
            assert ("terminal" in str(raised.value)) is (current in TERMINAL_STATUSES)

    def test_terminal_statuses_have_no_outgoing_moves(self) -> None:
        assert all(not LEGAL_TRANSITIONS[status] for status in TERMINAL_STATUSES)


class TestErrors:
    @pytest.mark.parametrize(
        "error_type",
        [
            FeedbackValidationError,
            FeedbackConfigurationError,
            FeedbackNotFoundError,
            FeedbackLifecycleError,
            FeedbackConflictError,
            FeedbackStoreError,
            FeedbackCorrelationError,
            FeedbackRoutingError,
            FeedbackHandlerError,
            FeedbackSubscriberError,
        ],
    )
    def test_every_error_is_a_feedback_manager_error(
        self, error_type: type[FeedbackManagerError]
    ) -> None:
        assert issubclass(error_type, FeedbackManagerError)

    def test_feedback_id_and_context_are_kept(self) -> None:
        feedback_id = uuid4()
        error = FeedbackStoreError("boom", feedback_id=feedback_id, stage="store")

        assert str(error) == f"boom (feedback_id={feedback_id})"
        assert error.context == {"stage": "store"}
        assert str(FeedbackStoreError("boom")) == "boom"

    def test_conflicts_are_lifecycle_errors_and_lookups_are_lookup_errors(self) -> None:
        assert issubclass(FeedbackConflictError, FeedbackLifecycleError)
        assert issubclass(FeedbackNotFoundError, LookupError)
