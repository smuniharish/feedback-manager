"""Recording LangGraph human-in-the-loop interrupts as feedback."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langgraph.types import Command
from pydantic_core import to_jsonable_python

from feedback_manager.core.categories import FeedbackCategory
from feedback_manager.core.context import ExecutionContext
from feedback_manager.core.sources import FeedbackSource
from feedback_manager.core.status import FeedbackStatus
from feedback_manager.errors import FeedbackNotFoundError, FeedbackValidationError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

    from langgraph.types import Interrupt

    from feedback_manager.api.manager import FeedbackManager
    from feedback_manager.core.events import FeedbackEvent
    from feedback_manager.core.targets import FeedbackTarget


class HumanInTheLoopBridge:
    """Keeps a feedback record of each human-in-the-loop pause and its answer.

    LangGraph owns pausing and resuming (``interrupt()`` and
    ``Command(resume=...)``). The bridge records why a graph paused, when
    `request` is called, and what the person decided, when `resolve` is called,
    so the decision is queryable, routable, and auditable like other feedback.

    ```python
    paused = await graph.ainvoke(inputs, config)
    snapshot = await graph.aget_state(config)
    (pending,) = extract_interrupts(paused)
    feedback = await bridge.request(
        target=FeedbackTarget(type=FeedbackTargetType.GRAPH, id="refunds"),
        interrupt=pending,
        execution_context=execution_context_from_snapshot(snapshot),
    )
    # ... a person decides ...
    await bridge.resolve(feedback.feedback_id, response="approve", approved=True)
    await graph.ainvoke(bridge.resume_command("approve"), config)
    ```

    Call `request` after the graph has paused, outside the graph. A node that
    calls `request` before ``interrupt()`` runs again when the graph resumes and
    would record the request twice.
    """

    def __init__(self, manager: FeedbackManager) -> None:
        self._manager = manager

    async def request(
        self,
        *,
        target: FeedbackTarget,
        interrupt: Interrupt | None = None,
        prompt: Any = None,
        execution_context: ExecutionContext | None = None,
        category: FeedbackCategory | str = FeedbackCategory.REQUEST_FOR_HUMAN,
        metadata: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> FeedbackEvent:
        """Record a pending request for a person's input.

        The request is ``SYSTEM`` feedback whose payload is ``{"prompt": ...}``.
        The prompt is converted to JSON-compatible data; values with no JSON
        form are stored as their string representation.

        Args:
            target: What the person is asked about.
            interrupt: The LangGraph ``Interrupt`` the graph paused on. Its
                value is the default prompt, and its ID is recorded as the
                context's ``interrupt_id``.
            prompt: What the person is asked; overrides the interrupt's value.
            execution_context: The execution that paused, for example from
                `execution_context_from_snapshot`.
            category: The feedback category.
            metadata: Application-defined details; JSON-compatible.
            idempotency_key: Pass one to make retried requests return the
                original record. An interrupt ID alone is not unique enough:
                successive ``interrupt()`` calls in one node share an ID.

        Raises:
            FeedbackValidationError: If neither ``interrupt`` nor ``prompt`` is
                given, or if ``execution_context`` names a different interrupt.
        """
        if interrupt is None and prompt is None:
            raise FeedbackValidationError("request() needs an interrupt or a prompt")
        if interrupt is not None:
            context = execution_context or ExecutionContext()
            if context.interrupt_id not in (None, interrupt.id):
                raise FeedbackValidationError(
                    f"execution_context.interrupt_id {context.interrupt_id!r} "
                    f"does not match the interrupt {interrupt.id!r}"
                )
            execution_context = context.model_copy(update={"interrupt_id": interrupt.id})
            if prompt is None:
                prompt = interrupt.value
        return await self._manager.submit(
            source=FeedbackSource.SYSTEM,
            category=category,
            target=target,
            payload={"prompt": to_jsonable_python(prompt, fallback=str)},
            metadata=metadata,
            execution_context=execution_context,
            idempotency_key=idempotency_key,
        )

    async def resolve(
        self, feedback_id: UUID, *, response: Any, approved: bool | None = None
    ) -> FeedbackEvent:
        """Record a person's response and close the request.

        The request becomes ``REJECTED`` when ``approved`` is ``False``, and
        ``RESOLVED`` otherwise, passing through ``ACKNOWLEDGED`` and ``HANDLED``
        as needed. Its resolution is ``{"response": ..., "approved": ...}``.
        Calling it again with the same decision returns the closed request.

        Args:
            feedback_id: The request returned by `request`.
            response: The person's response, as it will be passed to the graph.
            approved: Whether the person approved; ``None`` when the response
                is not an approval decision.

        Raises:
            FeedbackNotFoundError: If the request does not exist.
            FeedbackLifecycleError: If the request is already closed differently.
        """
        current = await self._manager.get(feedback_id)
        if current is None:
            raise FeedbackNotFoundError("unknown feedback event", feedback_id=feedback_id)
        resolution = {"response": to_jsonable_python(response, fallback=str), "approved": approved}
        if approved is False:
            return await self._manager.reject(feedback_id, resolution=resolution)
        if current.status is FeedbackStatus.RECEIVED:
            current = await self._manager.acknowledge(feedback_id)
        if current.status is FeedbackStatus.ACKNOWLEDGED:
            await self._manager.mark_handled(feedback_id)
        return await self._manager.resolve(feedback_id, resolution=resolution)

    @staticmethod
    def resume_command(response: Any, *, interrupt_id: str | None = None) -> Command[Any]:
        """Build the ``Command`` that resumes the paused graph with ``response``.

        Args:
            response: The value the pending ``interrupt()`` call returns.
            interrupt_id: Resume only this interrupt, when several are pending.
        """
        return Command(resume=response if interrupt_id is None else {interrupt_id: response})


__all__ = ["HumanInTheLoopBridge"]
