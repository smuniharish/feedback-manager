"""Classifying the exceptions LangChain and LangGraph report as feedback."""

from __future__ import annotations

import asyncio

from langgraph.errors import GraphBubbleUp, NodeCancelledError, NodeTimeoutError

from feedback_manager.core.categories import FeedbackCategory


def category_for_error(error: BaseException) -> FeedbackCategory:
    """Return the `FeedbackCategory` that describes ``error``.

    Cancellations (``asyncio.CancelledError`` and LangGraph's
    ``NodeCancelledError``) are ``CANCELLATION``; timeouts (``TimeoutError``
    and LangGraph's ``NodeTimeoutError``) are ``TIMEOUT``; every other error is
    ``FAILURE``.
    """
    if isinstance(error, asyncio.CancelledError | NodeCancelledError):
        return FeedbackCategory.CANCELLATION
    if isinstance(error, TimeoutError | NodeTimeoutError):
        return FeedbackCategory.TIMEOUT
    return FeedbackCategory.FAILURE


def is_reportable(error: BaseException) -> bool:
    """Return whether ``error`` is a failure worth recording.

    LangGraph raises ``GraphBubbleUp`` subclasses (interrupts, parent commands,
    drains) for control flow, not failures, and process-level exceptions such
    as ``KeyboardInterrupt`` are not feedback.
    """
    if isinstance(error, GraphBubbleUp):
        return False
    return isinstance(error, Exception | asyncio.CancelledError)


def failed_node(error: BaseException) -> str | None:
    """Return the node a LangGraph node error names: one that timed out or cancelled itself."""
    if isinstance(error, NodeTimeoutError | NodeCancelledError):
        return error.node
    return None


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


__all__ = ["category_for_error"]
