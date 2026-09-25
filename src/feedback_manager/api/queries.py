"""Feedback query re-export.

``FeedbackQuery`` is defined alongside the :class:`FeedbackStore` contract
(:mod:`feedback_manager.contracts.store`) since the two are tightly
coupled, but it is part of the public application-facing API, so it is
re-exported here as well.
"""

from feedback_manager.contracts.store import FeedbackQuery

__all__ = ["FeedbackQuery"]
