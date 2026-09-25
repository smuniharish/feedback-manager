"""Example 5 -- Evaluator feedback.

An evaluator (LLM-as-judge, rule-based checker, or human reviewer acting as
an evaluator) scores a generation's quality. FeedbackManager records this
feedback -- it does not implement the evaluator itself, and it does not
treat the score as ground truth; it only records what was said, by what,
and about which target (Section 24 of the spec).

Run with::

    uv run python examples/05_evaluator_feedback.py
"""

import asyncio
import os
import selectors
import sys

from feedback_manager import (
    ExecutionContext,
    FeedbackCategory,
    FeedbackManager,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)


async def evaluate(answer: str) -> dict[str, object]:
    """Stand-in for a real evaluator (e.g. an LLM-as-judge or a rules engine)."""
    return {"score": 0.42, "critique": "Answer is factually incorrect.", "policy_violation": False}


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


async def main() -> None:
    manager = await _build_manager()
    generation_id = "gen-77"

    result = await evaluate("The capital of Australia is Sydney.")
    feedback = await manager.submit(
        source=FeedbackSource.EVALUATOR,
        category=FeedbackCategory.QUALITY,
        target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id=generation_id),
        payload=result,
        execution_context=ExecutionContext(generation_id=generation_id),
        metadata={"evaluator_name": "factuality_judge_v1"},
    )
    print(f"Evaluator feedback recorded: id={feedback.feedback_id} payload={feedback.payload}")

    # Low scores can be routed to a human-review handler via a FeedbackRouter
    # rule; FeedbackManager only records and routes -- it never decides what
    # to do about a low score (Section 25: not a business decision engine).
    await manager.acknowledge(feedback.feedback_id)
    await manager.mark_handled(feedback.feedback_id)
    resolved = await manager.resolve(feedback.feedback_id, resolution={"routed_to": "human_review"})
    print(f"Evaluator feedback resolved: status={resolved.status}")


if __name__ == "__main__":
    _run(main())
