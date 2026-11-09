"""Classifying the exceptions LangChain and LangGraph report as feedback."""

from __future__ import annotations

from langgraph.errors import GraphBubbleUp, NodeCancelledError, NodeTimeoutError

from feedback_manager.core.categories import FeedbackCategory
from feedback_manager.integrations.base import FailureClassifier


class LangChainFailureClassifier(FailureClassifier):
    """Recognizes LangGraph's node cancellations, node timeouts, and control flow.

    Cancellations (``asyncio.CancelledError`` and LangGraph's
    ``NodeCancelledError``) are ``CANCELLATION``; timeouts (``TimeoutError``
    and LangGraph's ``NodeTimeoutError``) are ``TIMEOUT``; every other error is
    ``FAILURE``. LangGraph raises ``GraphBubbleUp`` subclasses (interrupts,
    parent commands, drains) for control flow; they are not reportable.
    """

    def category(self, error: BaseException) -> FeedbackCategory:
        """Return the `FeedbackCategory` that describes ``error``."""
        if isinstance(error, NodeCancelledError):
            return FeedbackCategory.CANCELLATION
        if isinstance(error, NodeTimeoutError):
            return FeedbackCategory.TIMEOUT
        return super().category(error)

    def is_reportable(self, error: BaseException) -> bool:
        """Return whether ``error`` is a failure rather than LangGraph control flow."""
        if isinstance(error, GraphBubbleUp):
            return False
        return super().is_reportable(error)

    def failed_node(self, error: BaseException) -> str | None:
        """Return the node that timed out or cancelled itself."""
        if isinstance(error, NodeTimeoutError | NodeCancelledError):
            return error.node
        return None


CLASSIFIER = LangChainFailureClassifier()


def category_for_error(error: BaseException) -> FeedbackCategory:
    """Return the `FeedbackCategory` that describes ``error``; see `LangChainFailureClassifier`."""
    return CLASSIFIER.category(error)


def caused_by(error: BaseException, origin: BaseException) -> bool:
    """Return whether ``origin`` is ``error`` or appears in its cause, context, or group."""
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if current is origin:
            return True
        if id(current) in seen:
            continue
        seen.add(id(current))
        pending.extend(
            linked for linked in (current.__cause__, current.__context__) if linked is not None
        )
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
    return False


__all__ = ["LangChainFailureClassifier", "category_for_error"]
