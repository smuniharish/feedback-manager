"""Optional durable feedback change-feed contract for crash recovery."""

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict

from feedback_manager.core.events import FeedbackEvent


class FeedbackChangePage(BaseModel):
    """Ordered persisted snapshots and the position after the last returned change."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    events: tuple[FeedbackEvent, ...]
    cursor: str | None
    has_more: bool = False


class FeedbackChangeSource(ABC):
    """A durable ordered log committed atomically with feedback writes.

    Retain changes until consumers have checkpointed them. Cursors are opaque
    positions; reading after the same cursor must replay the same ordered changes.
    A ``FeedbackStore`` provides this capability only by also subclassing
    ``FeedbackChangeSource``.
    """

    @abstractmethod
    async def read_changes(self, *, after: str | None, limit: int) -> FeedbackChangePage:
        """Return at most ``limit`` changes strictly after ``after``."""
