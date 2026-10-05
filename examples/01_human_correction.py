"""Record a person's correction of a generated answer and take it through its lifecycle.

Run with:

    uv run python examples/01_human_correction.py
"""

import asyncio

from feedback_manager import (
    ExecutionContext,
    FeedbackCategory,
    FeedbackManager,
    FeedbackQuery,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)


async def main() -> None:
    manager = FeedbackManager()
    answer = "The capital of Australia is Sydney."

    # A reviewer corrects the answer the model generated in conversation thread support-7.
    correction = await manager.submit(
        source=FeedbackSource.HUMAN,
        category=FeedbackCategory.CORRECTION,
        target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id="gen-42"),
        payload={"original": answer, "corrected": "The capital of Australia is Canberra."},
        execution_context=ExecutionContext(thread_id="support-7", generation_id="gen-42"),
    )
    print(f"Recorded correction {correction.feedback_id} ({correction.status})")

    # Your application applies the correction, then closes the feedback.
    await manager.acknowledge(correction.feedback_id)
    await manager.mark_handled(correction.feedback_id)
    resolved = await manager.resolve(correction.feedback_id, resolution={"applied_to": "faq-cache"})
    print(f"Resolved: {resolved.status}, resolution={resolved.resolution}")

    # Feedback about the same thread shares a correlation ID.
    thread_feedback = await manager.query(FeedbackQuery(correlation_id="support-7"))
    print(f"Feedback about thread support-7: {len(thread_feedback)} event(s)")


if __name__ == "__main__":
    asyncio.run(main())
