"""Link feedback to the langgraph-xai records of the run it is about.

Pass an `XAIRuntime` to `FeedbackManager` and feedback carries a
`FeedbackProvenanceReference`: during the run it includes the latest decision
and its evidence; after the run it is resolved from the provenance store.

Run with:

    uv run python examples/06_provenance.py
"""

import asyncio
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph_xai import DecisionType, EvidenceType, XAIRuntime

from feedback_manager import (
    ExecutionContext,
    FeedbackCategory,
    FeedbackManager,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.integrations.langgraph import execution_context_from_config

xai = XAIRuntime(application_id="support-bot", tenant_id="acme", graph_id="refunds")
manager = FeedbackManager(xai_runtime=xai)


class State(TypedDict, total=False):
    amount: float
    route: str


async def assess(state: State) -> State:
    evidence = await xai.record_evidence(
        EvidenceType.RULE, summary=f"Refund of {state['amount']} exceeds the 500 limit."
    )
    await xai.record_decision(
        "HUMAN_REVIEW",
        decision_type=DecisionType.ROUTING,
        candidate_actions=["AUTO_REFUND", "HUMAN_REVIEW"],
        evidence_ids=[evidence.id],
    )
    return {"route": "HUMAN_REVIEW"}


async def report(state: State, config: RunnableConfig) -> State:
    # Feedback submitted during the run refers to the run's latest decision.
    feedback = await manager.submit(
        source=FeedbackSource.AGENT,
        category=FeedbackCategory.REQUEST_FOR_HUMAN,
        target=FeedbackTarget(type=FeedbackTargetType.NODE, id="assess"),
        payload={"route": state["route"]},
        execution_context=execution_context_from_config(config, node_id="assess"),
    )
    assert feedback.provenance is not None
    print(f"In-run feedback provenance: {feedback.provenance.summary}")
    print(f"  decision={feedback.provenance.decision_id}")
    print(f"  evidence={feedback.provenance.evidence_ids}")
    return {}


def build_graph() -> Any:
    builder = StateGraph(State)
    builder.add_node("assess", assess)
    builder.add_node("report", report)
    builder.add_edge(START, "assess")
    builder.add_edge("assess", "report")
    builder.add_edge("report", END)
    return xai.instrument(builder.compile())


async def main() -> None:
    with xai.collect_runs() as runs:
        await build_graph().ainvoke({"amount": 900.0})
    (run,) = runs

    # Later, an evaluator reviews the finished run by its run ID.
    review = await manager.submit(
        source=FeedbackSource.EVALUATOR,
        category=FeedbackCategory.QUALITY,
        target=FeedbackTarget(type=FeedbackTargetType.RUN, id=str(run.run_id)),
        payload={"score": 0.9},
        execution_context=ExecutionContext(run_id=str(run.run_id), node_id="assess"),
    )
    assert review.provenance is not None
    print(f"Post-run feedback provenance: {review.provenance.summary}")
    print(f"  node execution={review.provenance.node_execution_id}")


if __name__ == "__main__":
    asyncio.run(main())
