"""Extension-point contracts (ABCs and Protocols) for feedback_manager.

See ``docs/architecture/EXTENSIBILITY_MODEL.md`` for the rationale behind
each ABC vs. Protocol choice.
"""

from feedback_manager.contracts.correlator import FeedbackCorrelator
from feedback_manager.contracts.handler import (
    FeedbackContext,
    FeedbackHandler,
    FeedbackHandlerResult,
)
from feedback_manager.contracts.policy import FeedbackLifecyclePolicy, FeedbackPolicy
from feedback_manager.contracts.provenance import FeedbackProvenanceAdapter
from feedback_manager.contracts.router import FeedbackRouter
from feedback_manager.contracts.serializer import DefaultFeedbackSerializer, FeedbackSerializer
from feedback_manager.contracts.store import FeedbackQuery, FeedbackStore
from feedback_manager.contracts.subscriber import FeedbackSubscriber

__all__ = [
    "DefaultFeedbackSerializer",
    "FeedbackContext",
    "FeedbackCorrelator",
    "FeedbackHandler",
    "FeedbackHandlerResult",
    "FeedbackLifecyclePolicy",
    "FeedbackPolicy",
    "FeedbackProvenanceAdapter",
    "FeedbackQuery",
    "FeedbackRouter",
    "FeedbackSerializer",
    "FeedbackStore",
    "FeedbackSubscriber",
]
