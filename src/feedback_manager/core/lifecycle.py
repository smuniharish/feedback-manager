"""The feedback lifecycle state machine.

Legal transitions are defined explicitly as a transition table rather than
scattered ``if`` statements, so the full set of legal/illegal moves is
visible in one place and can be unit tested exhaustively (see
``docs/architecture/LIFECYCLE.md``).

Rules:

* Same-state "transitions" (``X -> X``) are always legal and are a no-op --
  this makes lifecycle updates naturally idempotent for retried requests.
* Terminal states (``RESOLVED``, ``REJECTED``, ``CANCELLED``, ``EXPIRED``)
  have no outgoing transitions except to themselves.
* All other transitions must appear in :data:`LEGAL_TRANSITIONS` or they
  raise :class:`~feedback_manager.errors.FeedbackLifecycleError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from feedback_manager.core.status import TERMINAL_STATUSES, FeedbackStatus
from feedback_manager.errors import FeedbackLifecycleError

LEGAL_TRANSITIONS: dict[FeedbackStatus, frozenset[FeedbackStatus]] = {
    FeedbackStatus.CREATED: frozenset(
        {
            FeedbackStatus.RECEIVED,
            FeedbackStatus.CANCELLED,
            FeedbackStatus.EXPIRED,
        }
    ),
    FeedbackStatus.RECEIVED: frozenset(
        {
            FeedbackStatus.ACKNOWLEDGED,
            FeedbackStatus.REJECTED,
            FeedbackStatus.CANCELLED,
            FeedbackStatus.EXPIRED,
        }
    ),
    FeedbackStatus.ACKNOWLEDGED: frozenset(
        {
            FeedbackStatus.HANDLED,
            FeedbackStatus.REJECTED,
            FeedbackStatus.CANCELLED,
            FeedbackStatus.EXPIRED,
        }
    ),
    FeedbackStatus.HANDLED: frozenset(
        {
            FeedbackStatus.RESOLVED,
            FeedbackStatus.REJECTED,
            FeedbackStatus.CANCELLED,
        }
    ),
    FeedbackStatus.RESOLVED: frozenset(),
    FeedbackStatus.REJECTED: frozenset(),
    FeedbackStatus.CANCELLED: frozenset(),
    FeedbackStatus.EXPIRED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class LifecycleTransition:
    """A record of one lifecycle move, suitable for observability hooks."""

    feedback_id: UUID
    previous_status: FeedbackStatus
    new_status: FeedbackStatus
    idempotent: bool
    occurred_at: datetime


def is_legal_transition(current: FeedbackStatus, target: FeedbackStatus) -> bool:
    """Return whether moving from ``current`` to ``target`` is allowed."""
    if current == target:
        return True
    return target in LEGAL_TRANSITIONS.get(current, frozenset())


def validate_transition(
    feedback_id: UUID, current: FeedbackStatus, target: FeedbackStatus
) -> LifecycleTransition:
    """Validate a lifecycle transition, raising if it is illegal.

    Returns a :class:`LifecycleTransition` record describing the move
    (``idempotent=True`` when ``current == target``).
    """
    if current in TERMINAL_STATUSES and target != current:
        raise FeedbackLifecycleError(
            f"feedback is in terminal status {current!s} and cannot move to {target!s}",
            feedback_id=feedback_id,
            current_status=str(current),
            requested_status=str(target),
        )
    if not is_legal_transition(current, target):
        raise FeedbackLifecycleError(
            f"illegal feedback lifecycle transition {current!s} -> {target!s}",
            feedback_id=feedback_id,
            current_status=str(current),
            requested_status=str(target),
        )
    return LifecycleTransition(
        feedback_id=feedback_id,
        previous_status=current,
        new_status=target,
        idempotent=current == target,
        occurred_at=datetime.now(UTC),
    )


__all__ = ["LEGAL_TRANSITIONS", "LifecycleTransition", "is_legal_transition", "validate_transition"]
