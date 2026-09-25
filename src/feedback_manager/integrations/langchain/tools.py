"""A tool-agnostic helper for reporting tool feedback outside of callbacks.

Useful for tool implementations (including MCP-backed tools) that are not
invoked through a LangChain ``Runnable``/callback chain, where
:class:`~feedback_manager.integrations.langchain.callbacks.FeedbackCallbackHandler`
would not be triggered.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from feedback_manager.api.manager import FeedbackManager
from feedback_manager.core.context import ExecutionContext
from feedback_manager.core.sources import FeedbackSource
from feedback_manager.core.targets import FeedbackTarget, FeedbackTargetType
from feedback_manager.integrations.langchain.adapter import category_for_error


@asynccontextmanager
async def capture_tool_feedback(
    manager: FeedbackManager,
    *,
    tool_call_id: str,
    execution_context: ExecutionContext | None = None,
) -> AsyncIterator[None]:
    """Submit ``TOOL`` feedback if the wrapped block raises, then re-raise.

    Example::

        async with capture_tool_feedback(manager, tool_call_id=call_id):
            result = await my_mcp_tool.ainvoke(args)
    """
    try:
        yield
    except BaseException as error:
        await manager.submit(
            source=FeedbackSource.TOOL,
            category=category_for_error(error),
            target=FeedbackTarget(type=FeedbackTargetType.TOOL_CALL, id=tool_call_id),
            payload={"error": str(error), "error_type": type(error).__name__},
            execution_context=execution_context,
        )
        raise


__all__ = ["capture_tool_feedback"]
