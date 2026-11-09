"""Recording tool failures outside LangChain's callback system."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from feedback_manager.integrations.frameworks.langchain.failures import CLASSIFIER
from feedback_manager.integrations.tools import capture_tool_feedback as _capture

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from feedback_manager.api.manager import FeedbackManager
    from feedback_manager.core.context import ExecutionContext


@asynccontextmanager
async def capture_tool_feedback(
    manager: FeedbackManager,
    *,
    tool_call_id: str,
    tool_name: str | None = None,
    execution_context: ExecutionContext | None = None,
) -> AsyncGenerator[None]:
    """Record a failure of the wrapped tool call as ``TOOL`` feedback, then re-raise it.

    The generic `feedback_manager.integrations.capture_tool_feedback` with
    `LangChainFailureClassifier`, so LangGraph control flow, such as an
    interrupt, passes through without being recorded. Use it for tool code
    that does not run through a LangChain runnable, where
    `FeedbackCallbackHandler` sees nothing, such as direct calls to an MCP
    client:

    ```python
    async with capture_tool_feedback(manager, tool_call_id=call_id, tool_name="search"):
        result = await session.call_tool("search", arguments)
    ```

    Raises:
        FeedbackValidationError: On entry, if ``tool_call_id`` is not a
            non-empty string.
    """
    async with _capture(
        manager,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        execution_context=execution_context,
        classifier=CLASSIFIER,
    ):
        yield


__all__ = ["capture_tool_feedback"]
