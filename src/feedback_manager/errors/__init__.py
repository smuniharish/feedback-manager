"""The feedback-manager exception hierarchy."""

from feedback_manager.errors.exceptions import (
    FeedbackConfigurationError,
    FeedbackConflictError,
    FeedbackCorrelationError,
    FeedbackHandlerError,
    FeedbackLifecycleError,
    FeedbackManagerError,
    FeedbackNotFoundError,
    FeedbackRoutingError,
    FeedbackStoreError,
    FeedbackSubscriberError,
    FeedbackValidationError,
)

__all__ = [
    "FeedbackConfigurationError",
    "FeedbackConflictError",
    "FeedbackCorrelationError",
    "FeedbackHandlerError",
    "FeedbackLifecycleError",
    "FeedbackManagerError",
    "FeedbackNotFoundError",
    "FeedbackRoutingError",
    "FeedbackStoreError",
    "FeedbackSubscriberError",
    "FeedbackValidationError",
]
