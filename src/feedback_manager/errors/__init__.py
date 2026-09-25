"""Feedback-specific error hierarchy."""

from feedback_manager.errors.exceptions import (
    FeedbackConfigurationError,
    FeedbackCorrelationError,
    FeedbackHandlerError,
    FeedbackLifecycleError,
    FeedbackManagerError,
    FeedbackNotFoundError,
    FeedbackRoutingError,
    FeedbackSerializationError,
    FeedbackStoreError,
    FeedbackValidationError,
)

__all__ = [
    "FeedbackConfigurationError",
    "FeedbackCorrelationError",
    "FeedbackHandlerError",
    "FeedbackLifecycleError",
    "FeedbackManagerError",
    "FeedbackNotFoundError",
    "FeedbackRoutingError",
    "FeedbackSerializationError",
    "FeedbackStoreError",
    "FeedbackValidationError",
]
