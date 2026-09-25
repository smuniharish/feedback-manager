"""Extensible feedback routing."""

from feedback_manager.routing.default_router import DefaultFeedbackRouter
from feedback_manager.routing.rules import (
    RoutingPredicate,
    RoutingRule,
    by_category,
    by_source,
    by_target_type,
)

__all__ = [
    "DefaultFeedbackRouter",
    "RoutingPredicate",
    "RoutingRule",
    "by_category",
    "by_source",
    "by_target_type",
]
