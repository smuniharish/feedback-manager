"""Record feedback around a LangChain agent that uses real MCP tools.

Builds an agent with ``langchain.agents.create_agent`` and the tools of the
reference filesystem MCP server, attaches `FeedbackCallbackHandler` so any tool
or model failure is recorded automatically, and records an evaluator check of
the agent's answer.

Prerequisites:

- Node.js with ``npx``: the MCP server runs through
  ``npx @modelcontextprotocol/server-filesystem``.
- ``uv sync --group examples``.
- A chat model for ``init_chat_model``: set ``FEEDBACK_MANAGER_EXAMPLE_MODEL`` to
  a provider-prefixed model name such as ``openai:<model>``, and the provider's
  credentials, such as ``OPENAI_API_KEY`` (plus ``OPENAI_BASE_URL`` for an
  OpenAI-compatible endpoint).

Run with:

    uv run python examples/07_agent_mcp_create_agent.py
"""

import asyncio
import os
import sys
from pathlib import Path

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import StdioConnection

from feedback_manager import (
    FeedbackCategory,
    FeedbackManager,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.integrations.langchain import FeedbackCallbackHandler

WORKSPACE = Path(__file__).parent / "mcp_workspace"
MODEL_VARIABLE = "FEEDBACK_MANAGER_EXAMPLE_MODEL"


def npx(*arguments: str) -> StdioConnection:
    """An MCP stdio server launched with npx (a batch shim on Windows, so run it via cmd)."""
    if sys.platform == "win32":
        return {"transport": "stdio", "command": "cmd", "args": ["/c", "npx", *arguments]}
    return {"transport": "stdio", "command": "npx", "args": list(arguments)}


async def main() -> None:
    model = os.environ.get(MODEL_VARIABLE)
    if not model:
        sys.exit(f"Set {MODEL_VARIABLE} to a model for init_chat_model, such as openai:<model>.")
    manager = FeedbackManager()
    client = MultiServerMCPClient(
        {"filesystem": npx("-y", "@modelcontextprotocol/server-filesystem", str(WORKSPACE))}
    )
    tools = await client.get_tools()
    print(f"Loaded {len(tools)} MCP tools from the filesystem server")

    agent = create_agent(
        model=init_chat_model(model),
        tools=tools,
        system_prompt=(
            f"You answer questions about the files in {WORKSPACE}. "
            "Read files with the filesystem tools; answer in one sentence."
        ),
    )
    question = "What is the known issue in release_notes.md?"
    config: RunnableConfig = {
        "callbacks": [FeedbackCallbackHandler(manager)],
        "configurable": {"thread_id": "release-notes-qa"},
    }
    result = await agent.ainvoke({"messages": [{"role": "user", "content": question}]}, config)
    messages = result["messages"]
    tool_calls = [
        call["name"]
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    ]
    answer = messages[-1].text
    print(f"Tools used: {tool_calls}")
    print(f"Answer: {answer}")

    passed = "export" in answer.lower()
    await manager.submit(
        source=FeedbackSource.EVALUATOR,
        category=FeedbackCategory.QUALITY,
        target=FeedbackTarget(type=FeedbackTargetType.THREAD, id="release-notes-qa"),
        payload={"check": "mentions the /export known issue", "passed": passed, "answer": answer},
    )
    for event in await manager.query():
        print(
            f"Feedback: {event.source}/{event.category} {event.feedback_type or ''} {event.payload}"
        )


if __name__ == "__main__":
    asyncio.run(main())
