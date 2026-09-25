"""Framework-independent feedback domain model."""

from feedback_manager.core.categories import FeedbackCategory
from feedback_manager.core.context import CorrelationContext, ExecutionContext
from feedback_manager.core.events import FeedbackEvent
from feedback_manager.core.lifecycle import (
    LEGAL_TRANSITIONS,
    LifecycleTransition,
    is_legal_transition,
    validate_transition,
)
from feedback_manager.core.provenance import FeedbackProvenanceReference
from feedback_manager.core.sources import FeedbackSource
from feedback_manager.core.status import TERMINAL_STATUSES, FeedbackStatus
from feedback_manager.core.targets import FeedbackTarget, FeedbackTargetType

__all__ = [
    "LEGAL_TRANSITIONS",
    "TERMINAL_STATUSES",
    "CorrelationContext",
    "ExecutionContext",
    "FeedbackCategory",
    "FeedbackEvent",
    "FeedbackProvenanceReference",
    "FeedbackSource",
    "FeedbackStatus",
    "FeedbackTarget",
    "FeedbackTargetType",
    "LifecycleTransition",
    "is_legal_transition",
    "validate_transition",
]
