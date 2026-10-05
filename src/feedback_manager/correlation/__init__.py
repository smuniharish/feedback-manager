"""Correlation: grouping related feedback."""

from feedback_manager.correlation.correlator import (
    DefaultFeedbackCorrelator,
    default_correlation_id,
)

__all__ = ["DefaultFeedbackCorrelator", "default_correlation_id"]
