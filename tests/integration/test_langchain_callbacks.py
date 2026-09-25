"""Integration tests: real LangChain tool/callback failures becoming feedback."""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.tools import tool

from feedback_manager import FeedbackCategory, FeedbackManager, FeedbackSource, FeedbackTargetType
from feedback_manager.integrations.langchain import FeedbackCallbackHandler, capture_tool_feedback

pytestmark = pytest.mark.integration


@tool
async def _timeout_tool(x: int) -> int:
    """A tool that always times out, for testing tool-failure feedback."""
    raise TimeoutError("tool timed out")


@tool
async def _failing_tool(x: int) -> int:
    """A tool that always raises a generic failure."""
    raise ValueError("bad input")


async def test_tool_timeout_is_captured_as_feedback() -> None:
    manager = FeedbackManager()
    captured: list[object] = []

    async def subscriber(event: object) -> None:
        captured.append(event)

    manager.subscribe(subscriber)
    handler = FeedbackCallbackHandler(manager)

    with pytest.raises(TimeoutError):
        await _timeout_tool.ainvoke({"x": 1}, config={"callbacks": [handler]})

    await asyncio.sleep(0.05)
    assert len(captured) == 1
    event = captured[0]
    assert event.source == FeedbackSource.TOOL
    assert event.category == FeedbackCategory.TIMEOUT
    assert event.target.type == FeedbackTargetType.TOOL_CALL


async def test_tool_failure_is_captured_as_feedback() -> None:
    manager = FeedbackManager()
    captured: list[object] = []

    async def subscriber(event: object) -> None:
        captured.append(event)

    manager.subscribe(subscriber)
    handler = FeedbackCallbackHandler(manager)

    with pytest.raises(ValueError, match="bad input"):
        await _failing_tool.ainvoke({"x": 1}, config={"callbacks": [handler]})

    await asyncio.sleep(0.05)
    assert len(captured) == 1
    assert captured[0].category == FeedbackCategory.FAILURE


async def test_capture_tool_feedback_context_manager_reports_and_reraises() -> None:
    manager = FeedbackManager()

    async def flaky() -> None:
        async with capture_tool_feedback(manager, tool_call_id="call-xyz"):
            raise TimeoutError("mcp tool timed out")

    with pytest.raises(TimeoutError):
        await flaky()

    events = await manager.list()
    assert len(events) == 1
    assert events[0].category == FeedbackCategory.TIMEOUT
    assert events[0].target.id == "call-xyz"


async def test_llm_error_is_captured_as_generation_feedback() -> None:
    from uuid import uuid4

    manager = FeedbackManager()
    handler = FeedbackCallbackHandler(manager)
    await handler.on_llm_error(ValueError("bad prompt"), run_id=uuid4())
    events = await manager.list()
    assert len(events) == 1
    assert events[0].source == FeedbackSource.GENERATION
    assert events[0].target.type == FeedbackTargetType.GENERATION


async def test_chain_error_is_captured_as_agent_feedback() -> None:
    from uuid import uuid4

    manager = FeedbackManager()
    handler = FeedbackCallbackHandler(manager)
    await handler.on_chain_error(RuntimeError("chain broke"), run_id=uuid4())
    events = await manager.list()
    assert len(events) == 1
    assert events[0].source == FeedbackSource.AGENT
    assert events[0].target.type == FeedbackTargetType.RUN


async def test_retriever_error_is_captured_as_tool_feedback() -> None:
    from uuid import uuid4

    manager = FeedbackManager()
    handler = FeedbackCallbackHandler(manager)
    await handler.on_retriever_error(RuntimeError("retriever broke"), run_id=uuid4())
    events = await manager.list()
    assert len(events) == 1
    assert events[0].source == FeedbackSource.TOOL
    assert events[0].target.type == FeedbackTargetType.TOOL_RESULT
