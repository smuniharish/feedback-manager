"""Shared pytest fixtures for feedback_manager tests."""

from __future__ import annotations

import pytest

from feedback_manager import FeedbackManager


@pytest.fixture
def manager() -> FeedbackManager:
    return FeedbackManager()
