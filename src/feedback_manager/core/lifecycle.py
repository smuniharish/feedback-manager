"""The feedback lifecycle state machine.

Legal transitions are an explicit table, so every legal and illegal move is
visible in one place and can be tested exhaustively:

* A same-status "transition" (``X -> X``) is always legal and is a no-op, which
  makes retried lifecycle calls idempotent.
* Terminal statuses (``RESOLVED``, ``REJECTED``, ``CANCELLED``, ``EXPIRED``)
  have no outgoing transitions.
* Every other move must appear in `LEGAL_TRANSITIONS`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING

from feedback_manager.core.status import TERMINAL_STATUSES, FeedbackStatus
from feedback_manager.errors import FeedbackLifecycleError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

LEGAL_TRANSITIONS: Mapping[FeedbackStatus, frozenset[FeedbackStatus]] = MappingProxyType(
    {
        FeedbackStatus.CREATED: frozenset(
            {FeedbackStatus.RECEIVED, FeedbackStatus.CANCELLED, FeedbackStatus.EXPIRED}
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
            {FeedbackStatus.RESOLVED, FeedbackStatus.REJECTED, FeedbackStatus.CANCELLED}
        ),
        FeedbackStatus.RESOLVED: frozenset(),
        FeedbackStatus.REJECTED: frozenset(),
        FeedbackStatus.CANCELLED: frozenset(),
        FeedbackStatus.EXPIRED: frozenset(),
    }
)
"""The statuses each status may move to, excluding same-status no-ops."""


@dataclass(frozen=True, slots=True)
class LifecycleTransition:
    """A validated lifecycle move, as returned by `validate_transition`.

    Attributes:
        feedback_id: The feedback event the move applies to.
        previous_status: The status before the move.
        new_status: The status after the move.
        idempotent: Whether the move is a same-status no-op.
        occurred_at: When the move was validated (UTC).
    """

    feedback_id: UUID
    previous_status: FeedbackStatus
    new_status: FeedbackStatus
    idempotent: bool
    occurred_at: datetime


def is_legal_transition(current: FeedbackStatus, target: FeedbackStatus) -> bool:
    """Return whether moving from ``current`` to ``target`` is allowed."""
    return current == target or target in LEGAL_TRANSITIONS[current]


def validate_transition(
    feedback_id: UUID, current: FeedbackStatus, target: FeedbackStatus
) -> LifecycleTransition:
    """Validate a lifecycle move and describe it.

    Custom `FeedbackStore` implementations call this before persisting a
    transition, so they enforce the same state machine as the bundled stores.

    Returns:
        The validated move; ``idempotent`` is ``True`` when ``current == target``.

    Raises:
        FeedbackLifecycleError: If the move is not allowed.
    """
    if not is_legal_transition(current, target):
        reason = (
            f"feedback is {current} (terminal) and cannot move to {target}"
            if current in TERMINAL_STATUSES
            else f"illegal feedback lifecycle transition {current} -> {target}"
        )
        raise FeedbackLifecycleError(
            reason,
            feedback_id=feedback_id,
            current_status=current.value,
            requested_status=target.value,
        )
    return LifecycleTransition(
        feedback_id=feedback_id,
        previous_status=current,
        new_status=target,
        idempotent=current == target,
        occurred_at=datetime.now(UTC),
    )


__all__ = ["LEGAL_TRANSITIONS", "LifecycleTransition", "is_legal_transition", "validate_transition"]
