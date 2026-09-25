"""Unit tests for the open string-value domain types (sources, categories, targets)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from feedback_manager.core.categories import FeedbackCategory
from feedback_manager.core.sources import FeedbackSource
from feedback_manager.core.targets import FeedbackTarget, FeedbackTargetType


def test_well_known_source_constants_are_distinct() -> None:
    values = {
        FeedbackSource.HUMAN,
        FeedbackSource.AGENT,
        FeedbackSource.GENERATION,
        FeedbackSource.TOOL,
        FeedbackSource.EVALUATOR,
        FeedbackSource.APPLICATION,
        FeedbackSource.SYSTEM,
        FeedbackSource.EXTERNAL,
    }
    assert len(values) == 8


def test_source_is_a_plain_string_subclass() -> None:
    assert isinstance(FeedbackSource.HUMAN, str)
    assert FeedbackSource.HUMAN == "human"


def test_source_supports_arbitrary_extension() -> None:
    custom = FeedbackSource("mcp_server")
    assert custom == "mcp_server"
    assert isinstance(custom, FeedbackSource)


def test_category_supports_arbitrary_extension() -> None:
    custom = FeedbackCategory("business_policy_violation")
    assert custom == "business_policy_violation"


def test_target_type_supports_arbitrary_extension() -> None:
    custom = FeedbackTargetType("custom_widget")
    assert custom == "custom_widget"


class _Model(BaseModel):
    source: FeedbackSource
    category: FeedbackCategory


def test_open_values_work_as_pydantic_fields() -> None:
    model = _Model(source="human", category="rating")
    assert model.source == FeedbackSource.HUMAN
    assert model.category == FeedbackCategory.RATING
    dumped = model.model_dump()
    assert dumped == {"source": "human", "category": "rating"}


def test_target_requires_type_and_id() -> None:
    target = FeedbackTarget(type=FeedbackTargetType.TOOL_CALL, id="call-123")
    assert target.type == "tool_call"
    assert target.id == "call-123"


def test_target_is_frozen() -> None:
    target = FeedbackTarget(type=FeedbackTargetType.NODE, id="node-1")
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError on frozen model
        target.id = "other"  # type: ignore[misc]
