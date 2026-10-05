"""Routing feedback to handlers."""

from feedback_manager.routing.default_router import DefaultFeedbackRouter
from feedback_manager.routing.rules import (
    RoutingPredicate,
    RoutingRule,
    all_of,
    any_of,
    by_category,
    by_source,
    by_target_type,
)

__all__ = [
    "DefaultFeedbackRouter",
    "RoutingPredicate",
    "RoutingRule",
    "all_of",
    "any_of",
    "by_category",
    "by_source",
    "by_target_type",
]
