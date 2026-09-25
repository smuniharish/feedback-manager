"""Unit tests for the feedback lifecycle state machine."""

from __future__ import annotations

from itertools import pairwise
from uuid import uuid4

import pytest

from feedback_manager.core.lifecycle import is_legal_transition, validate_transition
from feedback_manager.core.status import TERMINAL_STATUSES, FeedbackStatus
from feedback_manager.errors import FeedbackLifecycleError

HAPPY_PATH = [
    FeedbackStatus.CREATED,
    FeedbackStatus.RECEIVED,
    FeedbackStatus.ACKNOWLEDGED,
    FeedbackStatus.HANDLED,
    FeedbackStatus.RESOLVED,
]


def test_happy_path_is_legal() -> None:
    for current, target in pairwise(HAPPY_PATH):
        assert is_legal_transition(current, target)


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATUSES))
def test_terminal_states_have_no_outgoing_transitions(terminal: FeedbackStatus) -> None:
    for candidate in FeedbackStatus:
        if candidate == terminal:
            continue
        assert not is_legal_transition(terminal, candidate)


def test_same_state_transition_is_always_legal_and_idempotent() -> None:
    for status in FeedbackStatus:
        transition = validate_transition(uuid4(), status, status)
        assert transition.idempotent is True


def test_illegal_transition_raises() -> None:
    with pytest.raises(FeedbackLifecycleError) as excinfo:
        validate_transition(uuid4(), FeedbackStatus.CREATED, FeedbackStatus.RESOLVED)
    assert excinfo.value.current_status == "created"
    assert excinfo.value.requested_status == "resolved"


def test_transition_out_of_terminal_state_raises() -> None:
    with pytest.raises(FeedbackLifecycleError):
        validate_transition(uuid4(), FeedbackStatus.RESOLVED, FeedbackStatus.RECEIVED)


def test_backwards_transition_is_illegal() -> None:
    assert not is_legal_transition(FeedbackStatus.HANDLED, FeedbackStatus.RECEIVED)


def test_skip_ahead_transition_is_illegal() -> None:
    assert not is_legal_transition(FeedbackStatus.CREATED, FeedbackStatus.HANDLED)


def test_cancellation_reachable_from_every_non_terminal_state() -> None:
    for status in FeedbackStatus:
        if status in TERMINAL_STATUSES:
            continue
        assert is_legal_transition(status, FeedbackStatus.CANCELLED)
