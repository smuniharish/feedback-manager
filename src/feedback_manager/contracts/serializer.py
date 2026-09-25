"""Structural (``Protocol``) contract for feedback serialization.

A ``Protocol`` is appropriate here: serialization is a pure, stateless
transform, and pydantic's own ``model_dump``/``model_dump_json`` already
satisfy this shape without any explicit inheritance -- which is exactly the
point of using a ``Protocol`` instead of an ``ABC``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from feedback_manager.core.events import FeedbackEvent


@runtime_checkable
class FeedbackSerializer(Protocol):
    """Converts a :class:`FeedbackEvent` to/from a JSON-compatible mapping."""

    def serialize(self, feedback: FeedbackEvent) -> dict[str, Any]: ...

    def deserialize(self, data: dict[str, Any]) -> FeedbackEvent: ...


class DefaultFeedbackSerializer:
    """Thin wrapper around pydantic's own serialization -- no custom protocol invented."""

    def serialize(self, feedback: FeedbackEvent) -> dict[str, Any]:
        return feedback.model_dump(mode="json")

    def deserialize(self, data: dict[str, Any]) -> FeedbackEvent:
        return FeedbackEvent.model_validate(data)


__all__ = ["DefaultFeedbackSerializer", "FeedbackSerializer"]
