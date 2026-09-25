"""Example 6 -- Provenance via langgraph-xai.

Instruments a real LangGraph graph with ``langgraph-xai``'s ``XAIRuntime``
and attaches the resulting execution provenance to feedback submitted from
inside a running node. Pass the runtime straight to ``FeedbackManager`` via
``xai_runtime`` -- it wires up the provenance adapter automatically.

Run with::

    uv run python examples/06_provenance.py
"""

import asyncio
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph_xai import XAIRuntime

from feedback_manager import (
    FeedbackCategory,
    FeedbackManager,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)


class State(TypedDict):
    answer: str


async def main() -> None:
    runtime = XAIRuntime(application_id="support-bot", tenant_id="acme-corp", graph_id="qa-graph")
    manager = FeedbackManager(xai_runtime=runtime)

    async def answer_node(state: State) -> State:
        # Feedback submitted while a node is executing automatically picks up
        # the active langgraph-xai run's provenance.
        feedback = await manager.submit(
            source=FeedbackSource.AGENT,
            category=FeedbackCategory.COMPLETION,
            target=FeedbackTarget(type=FeedbackTargetType.NODE, id="answer_node"),
            payload={"answer": "Canberra"},
        )
        print(f"Provenance attached: {feedback.provenance}")
        return {"answer": "Canberra"}

    graph = StateGraph(State)
    graph.add_node("answer_node", answer_node)
    graph.add_edge(START, "answer_node")
    graph.add_edge("answer_node", END)
    instrumented = runtime.instrument(graph.compile())

    result = await instrumented.ainvoke(
        {"answer": ""}, config={"configurable": {"thread_id": "prov-example"}}
    )
    print(f"Graph result: {result}")


if __name__ == "__main__":
    asyncio.run(main())
