"""Example 8 -- Real ``deepagents`` + real Playwright MCP tool + HITL feedback capture.

``deepagents`` is a batteries-included agent harness built on top of
LangGraph (planning, sub-agents, HITL approval gates, its own filesystem
tools, ...). This example:

- Points deepagents' *own* built-in filesystem tools (``ls``/``read_file``/
  ``edit_file``/...) at the real ``examples/mcp_workspace`` directory on disk
  via ``deepagents.backends.FilesystemBackend`` (deepagents' own filesystem
  tools would otherwise operate on an in-memory virtual filesystem -- using
  ``FilesystemBackend`` is the documented, supported way to make them touch
  real files, and deepagents explicitly recommends pairing it with HITL).
- Loads the real Playwright MCP server as an *additional* tool, so the
  agent also has a genuine browser-automation capability available (same
  MCP server as example 7).
- Configures a real approval gate on the destructive ``edit_file`` tool via
  deepagents' ``interrupt_on``, and uses FeedbackManager's existing
  ``HumanInTheLoopBridge`` (no bespoke interrupt engine) to turn that pause
  into a durable, queryable feedback record -- the "LangGraph interrupt ->
  FeedbackManager -> FeedbackEvent" flow from the package's design
  (LangGraph still owns pausing/resuming; FeedbackManager only owns the
  record of *why* and *what was decided*).

Run with::

    uv run python examples/08_agent_deepagents_mcp.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from feedback_manager import FeedbackManager, FeedbackTarget, FeedbackTargetType
from feedback_manager.integrations.langgraph import HumanInTheLoopBridge, extract_interrupts

WORKSPACE_DIR = Path(__file__).parent / "mcp_workspace"


def _npx_command() -> str:
    return "cmd" if sys.platform == "win32" else "npx"


def _npx_args(*rest: str) -> list[str]:
    if sys.platform == "win32":
        return ["/c", "npx", *rest]
    return list(rest)


async def build_playwright_tools() -> list:
    client = MultiServerMCPClient(
        {
            "playwright": {
                "transport": "stdio",
                "command": _npx_command(),
                "args": _npx_args("-y", "@playwright/mcp@latest", "--headless"),
            }
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
    manager = FeedbackManager()
    bridge = HumanInTheLoopBridge(manager)

    print("Connecting to the real Playwright MCP server...")
    browser_tools = await build_playwright_tools()
    print(f"Loaded {len(browser_tools)} real Playwright MCP tools (browser automation).")

    agent = create_deep_agent(
        model=build_llm(),
        tools=browser_tools,
        backend=FilesystemBackend(root_dir=WORKSPACE_DIR, virtual_mode=False),
        system_prompt=(
            "You maintain release_notes.md, which exists on the real filesystem. "
            "When asked to change a line, use the edit_file tool with a minimal, "
            "precise old_string/new_string pair."
        ),
        interrupt_on={"edit_file": True},
        checkpointer=InMemorySaver(),
    )
    print("Gating deepagents' built-in 'edit_file' tool behind human approval.")

    config = {"configurable": {"thread_id": "deepagents-demo"}}
    task = (
        "In release_notes.md, change the line about the known issue so it reads "
        "that the issue was fixed in v0.4.0."
    )
    print(f"\nRunning deepagents agent with real LLM + real filesystem backend.\nTask: {task}\n")

    paused = await agent.ainvoke({"messages": [{"role": "user", "content": task}]}, config=config)
    interrupts = extract_interrupts(paused)

    if not interrupts:
        print("Agent completed without requesting approval (model chose not to edit).")
        print(paused["messages"][-1].content)
        return

    hitl_request = interrupts[0].value
    action_requests = hitl_request["action_requests"]
    print(f"Agent paused for human approval of {len(action_requests)} tool call(s):")
    for request in action_requests:
        print(f"  - {request['name']}({request['args']})")

    feedback = await bridge.request(
        target=FeedbackTarget(type=FeedbackTargetType.TOOL_CALL, id="edit_file"),
        prompt=hitl_request,
    )
    print(f"\nFeedback request recorded: id={feedback.feedback_id} status={feedback.status}")

    # ... a human reviews the proposed edit in your application's UI and approves it ...
    resolved = await bridge.resolve(feedback.feedback_id, response="approved", approved=True)
    print(f"Human decision recorded: status={resolved.status}")

    decisions = [{"type": "approve"} for _ in action_requests]
    result = await agent.ainvoke(bridge.resume_command({"decisions": decisions}), config=config)
    print(f"\nAgent resumed and finished:\n{result['messages'][-1].content}")

    print("\nFinal file contents on the real disk:")
    print((WORKSPACE_DIR / "release_notes.md").read_text())


if __name__ == "__main__":
    asyncio.run(main())
