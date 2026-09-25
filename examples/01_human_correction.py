"""Example 1 -- Human correction.

An agent generates a response; a human corrects it. The correction is
submitted as feedback and correlated with the generation that produced the
original (incorrect) answer.

Run with::

    uv run python examples/01_human_correction.py
"""

import asyncio
import os
import selectors
import sys

from feedback_manager import (
    FeedbackCategory,
    FeedbackManager,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.core.context import ExecutionContext


async def generate_response(prompt: str) -> tuple[str, str]:
    """Stand-in for an LLM call. Returns (generation_id, answer)."""
    generation_id = "gen-42"
    answer = "The capital of Australia is Sydney."  # deliberately wrong
    return generation_id, answer


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

    generation_id, answer = await generate_response("What is the capital of Australia?")
    print(f"Agent answered: {answer!r}")

    # A human reviews the answer and submits a correction.
    correction = await manager.submit(
        source=FeedbackSource.HUMAN,
        category=FeedbackCategory.CORRECTION,
        target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id=generation_id),
        payload={
            "original_text": answer,
            "corrected_text": "The capital of Australia is Canberra.",
        },
        execution_context=ExecutionContext(generation_id=generation_id),
    )
    print(f"Correction recorded: id={correction.feedback_id} status={correction.status}")

    # Applications typically acknowledge + resolve once the correction has
    # been applied (e.g. fed back into a fine-tuning dataset or cache).
    await manager.acknowledge(correction.feedback_id)
    await manager.mark_handled(correction.feedback_id)
    resolved = await manager.resolve(
        correction.feedback_id, resolution={"applied": True, "channel": "manual_review"}
    )
    print(f"Correction resolved: status={resolved.status} metadata={resolved.metadata}")


if __name__ == "__main__":
    _run(main())
