"""Concrete, bundled :class:`FeedbackHandler` implementations.

Only an audit/logging handler ships with the package -- external
notification integrations (Slack, email, ticketing, ...) are explicitly out
of scope (Section 17) and belong in application code implementing
:class:`FeedbackHandler` directly.
"""

from __future__ import annotations

from feedback_manager import _logging
from feedback_manager.contracts.handler import (
    FeedbackContext,
    FeedbackHandler,
    FeedbackHandlerResult,
)
from feedback_manager.core.events import FeedbackEvent


class AuditFeedbackHandler(FeedbackHandler):
    """Records every feedback event it sees via structured logging."""

    def __init__(self, logger_name: str = "feedback_manager.audit") -> None:
        self._logger = _logging.get_logger(logger_name)

    async def handle(
        self, feedback: FeedbackEvent, context: FeedbackContext
    ) -> FeedbackHandlerResult:
        self._logger.info(
            "feedback audited",
            feedback_id=str(feedback.feedback_id),
            source=feedback.source,
            category=feedback.category,
            target_type=feedback.target.type,
            target_id=feedback.target.id,
            status=feedback.status,
        )
        return FeedbackHandlerResult(handled=True, detail="audited")


__all__ = ["AuditFeedbackHandler"]
