"""Example 7 -- Real ``langchain.agents.create_agent`` + real MCP tools + feedback capture.

This is not a mock: it spins up two *real* Model Context Protocol servers as
subprocesses (the reference filesystem server and the reference Playwright
server, both via ``npx``), wires their tools into a real LangChain agent
built with ``create_agent``, drives that agent with a real hosted LLM, and
captures feedback about what happened during the run using nothing more
than ``feedback_manager``'s existing LangChain integration (no bespoke
agent/tool-tracing infrastructure -- see ``FeedbackCallbackHandler`` and
``capture_tool_feedback``).

Prerequisites:
    - Node.js/``npx`` available on ``PATH``.
    - ``EXPLABS_API_KEY`` set in the environment.
    - ``uv sync --group examples`` (installs ``deepagents``,
      ``langchain-mcp-adapters``, ``langchain-openai``, ...).

Run with::

    uv run python examples/07_agent_mcp_create_agent.py
"""

from __future__ import annotations

import asyncio
import os
import selectors
import sys
from pathlib import Path

from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI

from feedback_manager import (
    FeedbackCategory,
    FeedbackManager,
    FeedbackQuery,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.integrations.langchain import FeedbackCallbackHandler

WORKSPACE_DIR = Path(__file__).parent / "mcp_workspace"


async def _build_manager() -> FeedbackManager:
    # Zero-config by default (in-memory store); set FEEDBACK_MANAGER_POSTGRES_DSN
    # to run this exact scenario against the real PostgreSQL store instead --
    # see docs/examples/grafana-observability.md.
    dsn = os.environ.get("FEEDBACK_MANAGER_POSTGRES_DSN")
    if not dsn:
        return FeedbackManager()
    from postgres_feedback_store import PostgresFeedbackStore

    store = await PostgresFeedbackStore.connect(dsn)
    return FeedbackManager(store=store)


def _run(coro):
    # psycopg's async mode needs a selector event loop; Windows defaults to
    # the proactor loop, so only override it there.
    if sys.platform == "win32":
        return asyncio.run(
            coro, loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        )
    return asyncio.run(coro)


def _npx_command() -> str:
    # On Windows, ``npx`` is a ``.ps1``/``.cmd`` shim that PowerShell's execution
    # policy usually refuses to run directly as a subprocess; invoking it through
    # the ``cmd`` shell (``cmd /c npx ...``) avoids that without changing the
    # user's system-wide execution policy.
    return "cmd" if sys.platform == "win32" else "npx"


def _npx_args(*rest: str) -> list[str]:
    if sys.platform == "win32":
        return ["/c", "npx", *rest]
    return list(rest)


async def build_mcp_tools() -> list:
    """Connect to the real filesystem + Playwright MCP servers and load their tools."""
    client = MultiServerMCPClient(
        {
            "filesystem": {
                "transport": "stdio",
                "command": _npx_command(),
                "args": _npx_args(
                    "-y", "@modelcontextprotocol/server-filesystem", str(WORKSPACE_DIR)
                ),
            },
            "playwright": {
                "transport": "stdio",
                "command": _npx_command(),
                "args": _npx_args("-y", "@playwright/mcp@latest", "--headless"),
            },
        }
    )
    return await client.get_tools()


def build_llm() -> ChatOpenAI:
    return ChatOpenAI(
        base_url=os.environ.get("EXPLABS_BASE_URL", "https://api.experientiallabs.ai/v1"),
        api_key=os.environ["EXPLABS_API_KEY"],
        model=os.environ.get("EXPLABS_MODEL", "gpt-5.6-luna"),
    )


async def main() -> None:
    manager = await _build_manager()
    handler = FeedbackCallbackHandler(manager)

    print("Connecting to real MCP servers (filesystem + playwright)...")
    tools = await build_mcp_tools()
    print(f"Loaded {len(tools)} real MCP tools: {[t.name for t in tools]}")

    agent = create_agent(
        model=build_llm(),
        tools=tools,
        system_prompt=(
            "You are a release-notes assistant. Use the filesystem tool to read "
            "release_notes.md and answer questions about it precisely and briefly."
        ),
    )

    question = (
        "Read release_notes.md in the workspace and tell me, in one sentence, "
        "what the known issue is."
    )
    print(f"\nRunning agent with real LLM + real MCP tools.\nQuestion: {question}\n")

    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": question}]},
        config={"callbacks": [handler], "configurable": {"thread_id": "mcp-demo"}},
    )
    final_message = result["messages"][-1]
    answer = final_message.content
    print(f"Agent answer:\n{answer}\n")

    # Quality-check the answer as an EVALUATOR-sourced feedback event -- this is
    # exactly the kind of automated "did the agent actually do the task"
    # feedback FeedbackManager is meant to carry, independent of any tool
    # failures the callback handler above may already have recorded.
    mentions_known_issue = "timeout" in answer.lower() or "export" in answer.lower()
    await manager.submit(
        source=FeedbackSource.EVALUATOR,
        category=FeedbackCategory.QUALITY if mentions_known_issue else FeedbackCategory.UNCERTAINTY,
        target=FeedbackTarget(type=FeedbackTargetType.RUN, id="mcp-demo"),
        payload={
            "check": "answer mentions the known issue from release_notes.md",
            "passed": mentions_known_issue,
            "answer": answer,
        },
    )

    events = await manager.query(FeedbackQuery())
    print(f"\nFeedback events captured this run: {len(events)}")
    for event in events:
        print(f"  - source={event.source} category={event.category} status={event.status}")


if __name__ == "__main__":
    _run(main())
