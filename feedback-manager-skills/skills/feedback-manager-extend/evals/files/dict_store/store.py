"""A hand-rolled feedback store that was never certified against the contract kit."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from feedback_manager import FeedbackEvent, FeedbackNotFoundError, FeedbackQuery, FeedbackStatus
from feedback_manager.contracts import FeedbackStore
from feedback_manager.core import JsonObject


class DictFeedbackStore(FeedbackStore):
    def __init__(self) -> None:
        self._rows: dict[UUID, FeedbackEvent] = {}

    async def create(self, feedback: FeedbackEvent) -> FeedbackEvent:
        self._rows[feedback.feedback_id] = feedback
        return feedback

    async def get(self, feedback_id: UUID) -> FeedbackEvent | None:
        return self._rows.get(feedback_id)

    async def transition(
        self,
        feedback_id: UUID,
        status: FeedbackStatus,
        *,
        expected: FeedbackStatus,
        resolution: JsonObject | None = None,
    ) -> FeedbackEvent:
        if feedback_id not in self._rows:
            raise FeedbackNotFoundError(str(feedback_id))
        updated = self._rows[feedback_id].with_status(status, resolution=resolution)
        self._rows[feedback_id] = updated
        return updated

    async def query(self, query: FeedbackQuery) -> Sequence[FeedbackEvent]:
        return list(self._rows.values())
