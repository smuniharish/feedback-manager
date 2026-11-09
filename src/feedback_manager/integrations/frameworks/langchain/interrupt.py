"""Recording LangGraph human-in-the-loop interrupts as feedback."""

from __future__ import annotations

from typing import Any

from langgraph.types import Command, Interrupt

from feedback_manager.integrations.base import HumanInTheLoopBridge


class LangGraphHumanInTheLoopBridge(HumanInTheLoopBridge[Interrupt, Command[Any]]):
    """Keeps a feedback record of each LangGraph interrupt and its answer.

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
    would record the request twice. Successive ``interrupt()`` calls in one
    node share an ID, so pass an ``idempotency_key`` to make retries safe.
    """

    def interrupt_id(self, interrupt: Interrupt) -> str:
        """Return the LangGraph interrupt's ID."""
        return interrupt.id

    def interrupt_value(self, interrupt: Interrupt) -> Any:
        """Return the value passed to ``interrupt()``."""
        return interrupt.value

    def resume_command(self, response: Any, *, interrupt_id: str | None = None) -> Command[Any]:
        """Build the ``Command`` that resumes the paused graph with ``response``.

        Args:
            response: The value the pending ``interrupt()`` call returns.
            interrupt_id: Resume only this interrupt, when several are pending.
        """
        return Command(resume=response if interrupt_id is None else {interrupt_id: response})


__all__ = ["LangGraphHumanInTheLoopBridge"]
