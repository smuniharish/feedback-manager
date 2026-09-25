"""Example 2 -- HITL approval via LangGraph interrupt/resume.

Uses a real, compiled LangGraph graph. LangGraph owns pausing and resuming
execution (``interrupt``/``Command(resume=...)``); FeedbackManager only
manages the feedback record describing *why* execution paused and what a
human decided.

Run with::

    uv run python examples/02_hitl_approval.py
"""

import asyncio
from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from feedback_manager import FeedbackManager, FeedbackTarget, FeedbackTargetType
from feedback_manager.integrations.langgraph import HumanInTheLoopBridge, extract_interrupts


class State(TypedDict):
    action: str


async def send_email_node(state: State) -> State:
    decision = HumanInTheLoopBridge.interrupt(
        {"question": "Approve sending this email to the customer?", "action": state["action"]}
    )
    return {"action": f"{state['action']} ({decision})"}


def build_graph() -> Any:
    graph = StateGraph(State)
    graph.add_node("send_email", send_email_node)
    graph.add_edge(START, "send_email")
    graph.add_edge("send_email", END)
    return graph.compile(checkpointer=InMemorySaver())


async def main() -> None:
    manager = FeedbackManager()
    bridge = HumanInTheLoopBridge(manager)
    compiled = build_graph()
    config = {"configurable": {"thread_id": "hitl-example-1"}}

    paused = await compiled.ainvoke({"action": "send_refund_email"}, config=config)
    interrupts = extract_interrupts(paused)
    prompt = interrupts[0].value
    print(f"Graph paused, asking a human: {prompt}")

    feedback = await bridge.request(
        target=FeedbackTarget(type=FeedbackTargetType.GRAPH, id="hitl-example-1"), prompt=prompt
    )
    print(f"Feedback request recorded: id={feedback.feedback_id} status={feedback.status}")

    # ... a human approves in your application's UI ...
    resolved = await bridge.resolve(feedback.feedback_id, response="approved", approved=True)
    print(f"Human decision recorded: status={resolved.status}")

    result = await compiled.ainvoke(bridge.resume_command("approved"), config=config)
    print(f"Graph resumed and finished: {result}")


if __name__ == "__main__":
    asyncio.run(main())
