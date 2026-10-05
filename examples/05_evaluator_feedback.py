"""Record evaluator scores and route the low ones to a human review queue.

feedback-manager records what an evaluator said and routes it; deciding what
a score means stays with your rules and handlers.

Run with:

    uv run python examples/05_evaluator_feedback.py
"""

import asyncio

from feedback_manager import (
    FeedbackCategory,
    FeedbackEvent,
    FeedbackManager,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.contracts import FeedbackHandler, FeedbackHandlerResult
from feedback_manager.routing import DefaultFeedbackRouter, RoutingRule

ANSWERS = {
    "gen-1": "Canberra is the capital of Australia.",
    "gen-2": "Sydney is the capital of Australia.",
    "gen-3": "Australia's capital city is Canberra.",
}


def judge(answer: str) -> dict[str, float | str]:
    """Stands in for an evaluator, such as an LLM-as-judge or a rules engine."""
    if "Canberra" in answer:
        return {"score": 0.95, "critique": "Correct."}
    return {"score": 0.2, "critique": "Names the wrong city."}


def low_score(feedback: FeedbackEvent) -> bool:
    score = feedback.payload.get("score")
    return isinstance(score, float) and score < 0.5


class ReviewQueue(FeedbackHandler):
    """Collects feedback that needs a person's attention."""

    def __init__(self) -> None:
        self.pending: list[str] = []

    async def handle(self, feedback: FeedbackEvent) -> FeedbackHandlerResult:
        self.pending.append(feedback.target.id)
        return FeedbackHandlerResult(handled=True, detail="queued for human review")


async def main() -> None:
    queue = ReviewQueue()
    router = DefaultFeedbackRouter([RoutingRule(predicate=low_score, handlers=[queue])])
    manager = FeedbackManager(router=router)

    for generation_id, answer in ANSWERS.items():
        verdict = judge(answer)
        await manager.submit(
            source=FeedbackSource.EVALUATOR,
            category=FeedbackCategory.QUALITY,
            target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id=generation_id),
            payload=verdict,
            metadata={"evaluator": "factuality-judge-v1"},
        )
        print(f"{generation_id}: score={verdict['score']} ({verdict['critique']})")

    print(f"Routed to human review: {queue.pending}")


if __name__ == "__main__":
    asyncio.run(main())
