"""Integration tests: real LangGraph interrupt/resume + FeedbackManager HITL bridge.

These tests exercise the actual ``langgraph`` package (compiled graphs,
checkpointer, ``interrupt``/``Command``) rather than mocking it.
"""

from __future__ import annotations

from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from feedback_manager import FeedbackManager, FeedbackStatus, FeedbackTarget, FeedbackTargetType
from feedback_manager.integrations.langgraph import HumanInTheLoopBridge, extract_interrupts

pytestmark = pytest.mark.integration


class _State(TypedDict):
    value: str


def _build_graph():
    async def node(state: _State) -> _State:
        answer = HumanInTheLoopBridge.interrupt("approve this action?")
        return {"value": answer}

    graph = StateGraph(_State)
    graph.add_node("node", node)
    graph.add_edge(START, "node")
    graph.add_edge("node", END)
    return graph.compile(checkpointer=InMemorySaver())


async def test_hitl_approval_flow_end_to_end() -> None:
    manager = FeedbackManager()
    bridge = HumanInTheLoopBridge(manager)
    compiled = _build_graph()
    config = {"configurable": {"thread_id": "hitl-approve"}}

    interrupted = await compiled.ainvoke({"value": ""}, config=config)
    interrupts = extract_interrupts(interrupted)
    assert len(interrupts) == 1
    assert interrupts[0].value == "approve this action?"

    feedback = await bridge.request(
        target=FeedbackTarget(type=FeedbackTargetType.GRAPH, id="hitl-approve"),
        prompt=interrupts[0].value,
    )
    assert feedback.status == FeedbackStatus.RECEIVED

    resumed = await compiled.ainvoke(bridge.resume_command("approved"), config=config)
    assert resumed == {"value": "approved"}

    resolved = await bridge.resolve(feedback.feedback_id, response="approved", approved=True)
    assert resolved.status == FeedbackStatus.RESOLVED
    assert resolved.metadata["resolution"]["approved"] is True


async def test_hitl_rejection_flow_terminates_as_rejected() -> None:
    manager = FeedbackManager()
    bridge = HumanInTheLoopBridge(manager)
    compiled = _build_graph()
    config = {"configurable": {"thread_id": "hitl-reject"}}

    await compiled.ainvoke({"value": ""}, config=config)
    feedback = await bridge.request(
        target=FeedbackTarget(type=FeedbackTargetType.GRAPH, id="hitl-reject"),
        prompt="approve this action?",
    )

    rejected = await bridge.resolve(
        feedback.feedback_id, response="denied: policy violation", approved=False
    )
    assert rejected.status == FeedbackStatus.REJECTED

    resumed = await compiled.ainvoke(bridge.resume_command("denied"), config=config)
    assert resumed == {"value": "denied"}
