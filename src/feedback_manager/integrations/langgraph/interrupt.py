"""Bridges LangGraph's native interrupt/resume mechanism to feedback records.

LangGraph owns pausing and resuming execution (``langgraph.types.interrupt``
and ``Command(resume=...)``); ``HumanInTheLoopBridge`` only manages the
:class:`~feedback_manager.core.events.FeedbackEvent` record that exists
*around* that interruption -- creating it when a human's input is requested,
and resolving it once a human has responded. This package never
reimplements LangGraph's interrupt engine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from feedback_manager.api.manager import FeedbackManager
from feedback_manager.core.categories import FeedbackCategory
from feedback_manager.core.context import ExecutionContext
from feedback_manager.core.events import FeedbackEvent
from feedback_manager.core.sources import FeedbackSource
from feedback_manager.core.targets import FeedbackTarget

if TYPE_CHECKING:
    from langgraph.types import Command


class HumanInTheLoopBridge:
    """Wraps a :class:`FeedbackManager` for LangGraph human-in-the-loop flows."""

    def __init__(self, manager: FeedbackManager) -> None:
        self._manager = manager

    async def request(
        self,
        *,
        target: FeedbackTarget,
        prompt: Any,
        execution_context: ExecutionContext | None = None,
        category: str = FeedbackCategory.REQUEST_FOR_HUMAN,
        metadata: dict[str, Any] | None = None,
    ) -> FeedbackEvent:
        """Record a pending human-feedback request.

        Call this before (or immediately after) invoking
        ``langgraph.types.interrupt(prompt)`` inside a graph node, so there
        is a durable feedback record describing the pause.
        """
        return await self._manager.submit(
            source=FeedbackSource.SYSTEM,
            category=category,
            target=target,
            payload={"prompt": prompt},
            execution_context=execution_context,
            metadata=metadata,
        )

    async def resolve(
        self,
        feedback_id: UUID,
        *,
        response: Any,
        approved: bool | None = None,
    ) -> FeedbackEvent:
        """Record a human's response and resolve the pending feedback event.

        ``approved`` is an optional convenience hint stored in the
        resolution metadata; it does not change how ``response`` is passed
        back into the graph via :meth:`resume_command`.
        """
        acknowledged = await self._manager.acknowledge(feedback_id)
        handled = await self._manager.mark_handled(acknowledged.feedback_id)
        if approved is False:
            return await self._manager.reject(handled.feedback_id, reason=str(response))
        return await self._manager.resolve(
            handled.feedback_id, resolution={"response": response, "approved": approved}
        )

    @staticmethod
    def resume_command(response: Any) -> Command[Any]:
        """Build the ``Command`` LangGraph expects to resume an interrupted graph."""
        from langgraph.types import Command

        return Command(resume=response)

    @staticmethod
    def interrupt(prompt: Any) -> Any:
        """Thin passthrough to ``langgraph.types.interrupt`` -- LangGraph still owns pausing."""
        from langgraph.types import interrupt

        return interrupt(prompt)


__all__ = ["HumanInTheLoopBridge"]
