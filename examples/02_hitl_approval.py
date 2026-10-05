"""Record a human-in-the-loop approval around a native LangGraph interrupt.

LangGraph pauses and resumes the graph. `HumanInTheLoopBridge` keeps a
feedback record of why it paused and what the reviewer decided.

Run with:

    uv run python examples/02_hitl_approval.py
"""

import asyncio
from typing import Any, NotRequired, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from feedback_manager import FeedbackManager, FeedbackTarget, FeedbackTargetType
from feedback_manager.integrations.langgraph import (
    HumanInTheLoopBridge,
    execution_context_from_snapshot,
    extract_interrupts,
)


class State(TypedDict):
    action: str
    decision: NotRequired[str]


def confirm(state: State) -> State:
    decision = interrupt({"question": "Send the refund email?", "action": state["action"]})
    return {"action": state["action"], "decision": decision}


def build_graph() -> Any:
    builder = StateGraph(State)
    builder.add_node("confirm", confirm)
    builder.add_edge(START, "confirm")
    builder.add_edge("confirm", END)
    return builder.compile(checkpointer=InMemorySaver())


async def main() -> None:
    manager = FeedbackManager()
    bridge = HumanInTheLoopBridge(manager)
    graph = build_graph()
    config: RunnableConfig = {"configurable": {"thread_id": "refund-1042"}}

    paused = await graph.ainvoke({"action": "send_refund_email"}, config)
    (pending,) = extract_interrupts(paused)
    snapshot = await graph.aget_state(config)

    request = await bridge.request(
        target=FeedbackTarget(type=FeedbackTargetType.NODE, id="confirm"),
        interrupt=pending,
        execution_context=execution_context_from_snapshot(snapshot),
    )
    print(f"Graph paused; approval request {request.feedback_id} is {request.status}")
    print(f"Prompt shown to the reviewer: {request.payload['prompt']}")

    # The reviewer approves in your application's UI.
    decision = await bridge.resolve(request.feedback_id, response="approved", approved=True)
    print(f"Decision recorded: {decision.status}, resolution={decision.resolution}")

    result = await graph.ainvoke(bridge.resume_command("approved"), config)
    print(f"Graph resumed and finished: {result}")


if __name__ == "__main__":
    asyncio.run(main())
