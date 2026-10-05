"""Gate a deepagents file edit behind human approval, and record the decision as feedback.

``deepagents`` builds a LangGraph agent with planning and file tools. Here its
``edit_file`` tool requires approval (``interrupt_on``), the agent is
instrumented by ``langgraph-xai``, and `HumanInTheLoopBridge` records the
approval request, linked to the tool call and to the paused run's provenance,
and the reviewer's decision. The agent edits a temporary copy of
``examples/mcp_workspace``, so the repository stays unchanged.

Prerequisites: ``uv sync --group examples`` and a chat model for
``init_chat_model``, configured as described in
``examples/07_agent_mcp_create_agent.py``.

Run with:

    uv run python examples/08_agent_deepagents_hitl.py
"""

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.chat_models import init_chat_model
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph_xai import XAIRuntime

from feedback_manager import FeedbackManager, FeedbackTarget, FeedbackTargetType
from feedback_manager.integrations.langgraph import (
    HumanInTheLoopBridge,
    execution_context_from_snapshot,
    extract_interrupts,
)

WORKSPACE = Path(__file__).parent / "mcp_workspace"
MODEL_VARIABLE = "FEEDBACK_MANAGER_EXAMPLE_MODEL"
TASK = (
    "In /release_notes.md, change the known issue about the /export endpoint "
    "to say it was fixed in v0.4.0."
)


async def main() -> None:
    model = os.environ.get(MODEL_VARIABLE)
    if not model:
        sys.exit(f"Set {MODEL_VARIABLE} to a model for init_chat_model, such as openai:<model>.")
    xai = XAIRuntime(application_id="docs-bot", tenant_id="acme", graph_id="release-notes")
    manager = FeedbackManager(xai_runtime=xai)
    bridge = HumanInTheLoopBridge(manager)

    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory)
        shutil.copy(WORKSPACE / "release_notes.md", workspace)
        agent = xai.instrument(
            create_deep_agent(
                model=init_chat_model(model),
                backend=FilesystemBackend(root_dir=workspace),
                system_prompt="You maintain /release_notes.md. Make minimal, exact edits.",
                interrupt_on={"edit_file": True},
                checkpointer=InMemorySaver(),
            )
        )
        config: RunnableConfig = {"configurable": {"thread_id": "release-notes-edit"}}

        paused = await agent.ainvoke({"messages": [{"role": "user", "content": TASK}]}, config)
        interrupts = extract_interrupts(paused)
        if not interrupts:
            print("The agent finished without proposing an edit.")
            return
        (pending,) = interrupts
        snapshot = await agent.aget_state(config)
        edits = [
            call
            for call in snapshot.values["messages"][-1].tool_calls
            if call["name"] == "edit_file"
        ]
        for edit in edits:
            print(f"The agent proposes edit_file({edit['args']})")
        # One pause can hold several proposed edits; a single one is the tool call itself.
        target = (
            FeedbackTarget(type=FeedbackTargetType.TOOL_CALL, id=edits[0]["id"])
            if len(edits) == 1
            else FeedbackTarget(type=FeedbackTargetType.THREAD, id="release-notes-edit")
        )

        request = await bridge.request(
            target=target,
            interrupt=pending,
            execution_context=execution_context_from_snapshot(snapshot),
        )
        provenance = request.provenance
        print(f"Approval request {request.feedback_id} ({request.status})")
        print(f"  linked to: {provenance.summary if provenance else 'no provenance'}")

        # A reviewer approves every proposed edit in your application's UI.
        response = {"decisions": [{"type": "approve"} for _ in pending.value["action_requests"]]}
        decision = await bridge.resolve(request.feedback_id, response=response, approved=True)
        print(f"Decision recorded: {decision.status}, resolution={decision.resolution}")
        await agent.ainvoke(bridge.resume_command(response), config)

        print("Edited file:")
        print((workspace / "release_notes.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    asyncio.run(main())
