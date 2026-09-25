"""Predicate-based routing rules."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from feedback_manager.contracts.handler import FeedbackHandler
from feedback_manager.core.events import FeedbackEvent

RoutingPredicate = Callable[[FeedbackEvent], bool]


@dataclass(frozen=True, slots=True)
class RoutingRule:
    """Pairs a predicate with the handlers that should run when it matches."""

    predicate: RoutingPredicate
    handlers: tuple[FeedbackHandler, ...]
    name: str = "rule"


def by_source(source: str) -> RoutingPredicate:
    return lambda feedback: feedback.source == source


def by_category(category: str) -> RoutingPredicate:
    return lambda feedback: feedback.category == category


def by_target_type(target_type: str) -> RoutingPredicate:
    return lambda feedback: feedback.target.type == target_type


def any_of(*predicates: RoutingPredicate) -> RoutingPredicate:
    return lambda feedback: any(predicate(feedback) for predicate in predicates)


def all_of(*predicates: RoutingPredicate) -> RoutingPredicate:
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
