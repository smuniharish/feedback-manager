"""Recording tool failures outside LangChain's callback system."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from pydantic import ValidationError

from feedback_manager import _logging
from feedback_manager.core.context import ExecutionContext
from feedback_manager.core.sources import FeedbackSource
from feedback_manager.core.targets import FeedbackTarget, FeedbackTargetType
from feedback_manager.errors import FeedbackValidationError
from feedback_manager.integrations.langchain.failures import category_for_error, is_reportable

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from feedback_manager.api.manager import FeedbackManager

_logger = _logging.get_logger("feedback_manager.integrations.langchain")


@asynccontextmanager
async def capture_tool_feedback(
    manager: FeedbackManager,
    *,
    tool_call_id: str,
    tool_name: str | None = None,
    execution_context: ExecutionContext | None = None,
) -> AsyncGenerator[None]:
    """Record a failure of the wrapped tool call as ``TOOL`` feedback, then re-raise it.

    Use it for tool code that does not run through a LangChain runnable, where
    `FeedbackCallbackHandler` sees nothing, such as direct calls to an MCP
    client:

    ```python
    async with capture_tool_feedback(manager, tool_call_id=call_id, tool_name="search"):
        result = await session.call_tool("search", arguments)
    ```

    The original exception always propagates unchanged. If the feedback
    cannot be recorded, that failure is logged and attached to the original
    exception as a note instead of replacing it. LangGraph control flow, such
    as an interrupt, and the cancellation of the task running the call pass
    through without being recorded: a cancellation comes from outside the
    tool call, and is recorded, if at all, for the run that was cancelled.

    Args:
        manager: Receives the feedback.
        tool_call_id: The tool call the feedback is about.
        tool_name: The tool's name, recorded as the payload's ``operation``.
        execution_context: The execution the tool call belongs to; its
            ``tool_call_id`` is set to ``tool_call_id``.

    Raises:
        FeedbackValidationError: On entry, if ``tool_call_id`` is not a
            non-empty string.
    """
    try:
        target = FeedbackTarget(type=FeedbackTargetType.TOOL_CALL, id=tool_call_id)
    except ValidationError as exc:
        raise FeedbackValidationError(f"invalid tool_call_id: {tool_call_id!r}") from exc
    context = (execution_context or ExecutionContext()).model_copy(
        update={"tool_call_id": tool_call_id}
    )
    try:
        yield
    except Exception as error:
        if is_reportable(error):
            try:
                await manager.submit(
                    source=FeedbackSource.TOOL,
                    category=category_for_error(error),
                    feedback_type="tool_error",
                    target=target,
                    payload={
                        "error": str(error),
                        "error_type": type(error).__name__,
                        "operation": tool_name,
                    },
                    execution_context=context,
                )
            except Exception as feedback_error:
                error.add_note(
                    "feedback-manager could not record this failure: "
                    f"{type(feedback_error).__name__}: {feedback_error}"
                )
                _logger.warning(
                    "tool failure not recorded",
                    tool_call_id=tool_call_id,
                    error_type=type(feedback_error).__name__,
                    exc_info=feedback_error,
                )
        raise


__all__ = ["capture_tool_feedback"]
