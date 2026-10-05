"""Record a generation the user stopped mid-stream, with the partial output.

The application owns streaming; it reports the interruption as feedback and
still lets the cancellation propagate.

Run with:

    uv run python examples/04_generation_interruption.py
"""

import asyncio

from feedback_manager import (
    ExecutionContext,
    FeedbackCategory,
    FeedbackManager,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)

TOKENS = ["Canberra", " is", " the", " capital", " of", " Australia", "."]


async def stream_answer(manager: FeedbackManager, generation_id: str) -> str:
    """Stream an answer token by token and report how the generation ended."""
    target = FeedbackTarget(type=FeedbackTargetType.GENERATION, id=generation_id)
    context = ExecutionContext(generation_id=generation_id)
    received: list[str] = []
    try:
        for token in TOKENS:
            await asyncio.sleep(0.02)  # stands in for the model streaming a token
            received.append(token)
    except asyncio.CancelledError:
        await manager.submit(
            source=FeedbackSource.GENERATION,
            category=FeedbackCategory.INTERRUPTION,
            feedback_type="generation_interrupted",
            target=target,
            payload={"partial_output": "".join(received), "tokens": len(received)},
            execution_context=context,
        )
        raise
    await manager.submit(
        source=FeedbackSource.GENERATION,
        category=FeedbackCategory.COMPLETION,
        feedback_type="generation_completed",
        target=target,
        payload={"tokens": len(received)},
        execution_context=context,
    )
    return "".join(received)


async def main() -> None:
    manager = FeedbackManager()

    print(f"Completed: {await stream_answer(manager, 'gen-100')!r}")

    stopped = asyncio.create_task(stream_answer(manager, "gen-101"))
    await asyncio.sleep(0.07)  # the user presses "stop"
    stopped.cancel()
    try:
        await stopped
    except asyncio.CancelledError:
        print("Generation gen-101 was stopped by the user.")

    for event in await manager.query():
        print(f"{event.target.id}: {event.category} {event.payload}")


if __name__ == "__main__":
    asyncio.run(main())
