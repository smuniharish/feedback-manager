"""The bundled audit handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from feedback_manager import _logging
from feedback_manager.contracts.handler import FeedbackHandler, FeedbackHandlerResult

if TYPE_CHECKING:
    from feedback_manager.core.events import FeedbackEvent


class AuditFeedbackHandler(FeedbackHandler):
    """Writes one structured log record per event it handles.

    The record identifies the event and its target but never contains the
    payload, so it is safe to keep in ordinary logs.
    """

    def __init__(self, logger_name: str = "feedback_manager.audit") -> None:
        self._logger = _logging.get_logger(logger_name)

    async def handle(self, feedback: FeedbackEvent) -> FeedbackHandlerResult:
        """Log ``feedback`` and report it as handled."""
        self._logger.info(
            "feedback audited",
            feedback_id=str(feedback.feedback_id),
            source=str(feedback.source),
            category=str(feedback.category),
            target_type=str(feedback.target.type),
            target_id=feedback.target.id,
            status=feedback.status.value,
            correlation_id=feedback.correlation_id,
        )
        return FeedbackHandlerResult(handled=True, detail="audited")


__all__ = ["AuditFeedbackHandler"]
