"""Concrete, bundled :class:`FeedbackHandler` implementations.

Only an audit/logging handler ships with the package -- external
notification integrations (Slack, email, ticketing, ...) are explicitly out
of scope (Section 17) and belong in application code implementing
:class:`FeedbackHandler` directly.
"""

from __future__ import annotations

import logging

from feedback_manager.contracts.handler import (
    FeedbackContext,
    FeedbackHandler,
    FeedbackHandlerResult,
)
from feedback_manager.core.events import FeedbackEvent


class AuditFeedbackHandler(FeedbackHandler):
    """Records every feedback event it sees via structured logging."""

    def __init__(self, logger_name: str = "feedback_manager.audit") -> None:
        self._logger = logging.getLogger(logger_name)

    async def handle(
        self, feedback: FeedbackEvent, context: FeedbackContext
    ) -> FeedbackHandlerResult:
        self._logger.info(
            "feedback audited id=%s source=%s category=%s target=%s:%s status=%s",
            feedback.feedback_id,
            feedback.source,
            feedback.category,
            feedback.target.type,
            feedback.target.id,
            feedback.status,
        )
        return FeedbackHandlerResult(handled=True, detail="audited")


__all__ = ["AuditFeedbackHandler"]
