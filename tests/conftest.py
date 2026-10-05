"""Pytest configuration: Hypothesis profiles and shared fixtures."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from hypothesis import HealthCheck, settings

from feedback_manager import FeedbackManager, FeedbackTarget, FeedbackTargetType

if TYPE_CHECKING:
    from feedback_manager.observability import ObservabilityEvent

settings.register_profile(
    "default",
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile(
    "ci",
    parent=settings.get_profile("default"),
    max_examples=300,
    derandomize=True,
    print_blob=True,
)
settings.register_profile(
    "thorough",
    parent=settings.get_profile("default"),
    max_examples=2_000,
    print_blob=True,
)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "default"))


class RecordingSink:
    """An observability sink that keeps every event, for assertions."""

    def __init__(self) -> None:
        self.events: list[ObservabilityEvent] = []

    def emit(self, event: ObservabilityEvent) -> None:
        self.events.append(event)

    @property
    def names(self) -> list[str]:
        return [event.name for event in self.events]


@pytest.fixture
def sink() -> RecordingSink:
    return RecordingSink()


@pytest.fixture
def manager(sink: RecordingSink) -> FeedbackManager:
    return FeedbackManager(observability_sink=sink)


@pytest.fixture
def target() -> FeedbackTarget:
    return FeedbackTarget(type=FeedbackTargetType.GENERATION, id="gen-1")
