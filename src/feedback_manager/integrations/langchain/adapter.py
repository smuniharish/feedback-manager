"""Thin adapter helpers shared by the LangChain integration modules."""

from __future__ import annotations

from feedback_manager.core.categories import FeedbackCategory


def category_for_error(error: BaseException) -> str:
    """Map a raised exception to a :class:`FeedbackCategory`.

    ``TimeoutError`` (and subclasses, including ``asyncio.TimeoutError`` on
    Python 3.12) maps to ``TIMEOUT``; ``asyncio.CancelledError`` maps to
    ``CANCELLATION``; everything else maps to ``FAILURE``.
    """
    import asyncio

    if isinstance(error, asyncio.CancelledError):
        return FeedbackCategory.CANCELLATION
    if isinstance(error, TimeoutError):
        return FeedbackCategory.TIMEOUT
    return FeedbackCategory.FAILURE


__all__ = ["category_for_error"]
