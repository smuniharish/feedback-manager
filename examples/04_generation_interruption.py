"""Example 4 -- Generation lifecycle feedback (interruption/failure).

FeedbackManager does not implement a token-streaming engine -- use
LangChain/LangGraph's own streaming for that. This example shows how an
application reports meaningful generation lifecycle events (started,
interrupted, failed) as feedback so they can be queried and correlated
alongside human/tool feedback about the same generation.

Run with::

    uv run python examples/04_generation_interruption.py
"""

import asyncio

from feedback_manager import (
    FeedbackCategory,
    FeedbackManager,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.core.context import ExecutionContext


async def run_generation(
    manager: FeedbackManager, generation_id: str, *, should_cancel: bool
) -> None:
    context = ExecutionContext(generation_id=generation_id)
    await manager.submit(
        source=FeedbackSource.GENERATION,
        category=FeedbackCategory.COMMENT,
        feedback_type="generation_started",
        target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id=generation_id),
        execution_context=context,
    )

    try:
        if should_cancel:
            raise asyncio.CancelledError
        await asyncio.sleep(0)  # stand-in for the actual model call
        await manager.submit(
            source=FeedbackSource.GENERATION,
            category=FeedbackCategory.COMPLETION,
            feedback_type="generation_completed",
            target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id=generation_id),
            execution_context=context,
        )
    except asyncio.CancelledError:
        await manager.submit(
            source=FeedbackSource.GENERATION,
            category=FeedbackCategory.INTERRUPTION,
            feedback_type="generation_interrupted",
            target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id=generation_id),
            execution_context=context,
        )


async def main() -> None:
    manager = FeedbackManager()

    await run_generation(manager, "gen-100", should_cancel=False)
    await run_generation(manager, "gen-101", should_cancel=True)

    for event in await manager.list():
        print(f"{event.feedback_type}: {event.category} for {event.target.id}")


if __name__ == "__main__":
    asyncio.run(main())
