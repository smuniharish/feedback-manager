"""A thin :class:`AsyncCallbackHandler` that turns LangChain lifecycle errors
into :class:`~feedback_manager.core.events.FeedbackEvent` submissions.

This uses LangChain's existing callback mechanism -- it does not invent a
new hook system. Only failure/error callbacks are translated automatically;
success paths are intentionally left to the application to report
explicitly (via ``FeedbackManager.submit`` or evaluator integrations),
since "success" is not inherently feedback.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.runnables import RunnableConfig

from feedback_manager.api.manager import FeedbackManager
from feedback_manager.core.sources import FeedbackSource
from feedback_manager.core.targets import FeedbackTarget, FeedbackTargetType
from feedback_manager.integrations.langchain.adapter import category_for_error
from feedback_manager.integrations.langgraph.adapter import execution_context_from_config


class FeedbackCallbackHandler(AsyncCallbackHandler):
    """Reports tool/LLM/chain errors observed via LangChain callbacks as feedback."""

    def __init__(self, manager: FeedbackManager, *, config: RunnableConfig | None = None) -> None:
        self._manager = manager
        self._config = config

    async def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        await self._submit(
            source=FeedbackSource.TOOL,
            target=FeedbackTarget(type=FeedbackTargetType.TOOL_CALL, id=str(run_id)),
            error=error,
            tool_call_id=str(run_id),
        )

    async def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        await self._submit(
            source=FeedbackSource.GENERATION,
            target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id=str(run_id)),
            error=error,
            generation_id=str(run_id),
        )

    async def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        await self._submit(
            source=FeedbackSource.AGENT,
            target=FeedbackTarget(type=FeedbackTargetType.RUN, id=str(run_id)),
            error=error,
        )

    async def on_retriever_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        await self._submit(
            source=FeedbackSource.TOOL,
            target=FeedbackTarget(type=FeedbackTargetType.TOOL_RESULT, id=str(run_id)),
            error=error,
            tool_call_id=str(run_id),
        )

    async def _submit(
        self,
        *,
        source: str,
        target: FeedbackTarget,
        error: BaseException,
        tool_call_id: str | None = None,
        generation_id: str | None = None,
    ) -> None:
        category = category_for_error(error)
        execution_context = execution_context_from_config(
            self._config, tool_call_id=tool_call_id, generation_id=generation_id
        )
        await self._manager.submit(
            source=source,
            category=category,
            target=target,
            payload={"error": str(error), "error_type": type(error).__name__},
            execution_context=execution_context,
        )


__all__ = ["FeedbackCallbackHandler"]
