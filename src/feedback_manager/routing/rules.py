"""Predicate-based routing rules."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from feedback_manager.contracts.handler import FeedbackHandler
    from feedback_manager.core.events import FeedbackEvent

type RoutingPredicate = Callable[[FeedbackEvent], bool]
"""Decides whether a `RoutingRule` applies to an event."""


@dataclass(frozen=True, slots=True)
class RoutingRule:
    """Pairs a predicate with the handlers to run when it matches.

    Attributes:
        predicate: Decides whether the rule applies to an event.
        handlers: The handlers to run, in order. Any sequence is accepted and
            stored as a tuple.
        name: A label for the rule, used in logs and debugging.
    """

    predicate: RoutingPredicate
    handlers: Sequence[FeedbackHandler]
    name: str = "rule"

    def __post_init__(self) -> None:
        object.__setattr__(self, "handlers", tuple(self.handlers))


def by_source(source: str) -> RoutingPredicate:
    """Match events from ``source``."""
    return lambda feedback: feedback.source == source


def by_category(category: str) -> RoutingPredicate:
    """Match events of ``category``."""
    return lambda feedback: feedback.category == category


def by_target_type(target_type: str) -> RoutingPredicate:
    """Match events whose target is of ``target_type``."""
    return lambda feedback: feedback.target.type == target_type


def any_of(*predicates: RoutingPredicate) -> RoutingPredicate:
    """Match events that satisfy at least one of ``predicates``."""
    return lambda feedback: any(predicate(feedback) for predicate in predicates)


def all_of(*predicates: RoutingPredicate) -> RoutingPredicate:
    """Match events that satisfy every one of ``predicates``."""
    return lambda feedback: all(predicate(feedback) for predicate in predicates)


__all__ = [
    "RoutingPredicate",
    "RoutingRule",
    "all_of",
    "any_of",
    "by_category",
    "by_source",
    "by_target_type",
]
