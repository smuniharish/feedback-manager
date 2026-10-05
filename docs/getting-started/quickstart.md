# Quickstart

This page takes one piece of feedback through its whole life: a person corrects
a generated answer, your application applies the correction, and the feedback
is resolved and queried back.

## Submit, resolve, and query feedback

```python
import asyncio

from feedback_manager import (
    ExecutionContext,
    FeedbackManager,
    FeedbackQuery,
    FeedbackTarget,
    FeedbackTargetType,
)


async def main() -> None:
    manager = FeedbackManager()

    # A person corrects a generated answer in conversation "support-7".
    feedback = await manager.submit(
        source="human",
        category="correction",
        target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id="gen-42"),
        payload={"corrected_text": "Canberra is the capital of Australia."},
        execution_context=ExecutionContext(thread_id="support-7"),
    )
    print(f"{feedback.status}: correlated by {feedback.correlation_id}")

    # Take it through its lifecycle once the correction is applied.
    await manager.acknowledge(feedback.feedback_id)
    await manager.mark_handled(feedback.feedback_id)
    resolved = await manager.resolve(feedback.feedback_id, resolution={"applied_to": "faq"})
    print(f"{resolved.status}: {resolved.resolution}")

    # Query it back, like any other feedback.
    for event in await manager.query(FeedbackQuery(correlation_id="support-7")):
        print(event.source, event.category, event.target.id, event.status)


asyncio.run(main())
```

Output:

```text
received: correlated by support-7
resolved: {'applied_to': 'faq'}
human correction gen-42 resolved
```

## What happened

1. **`submit`** validated the input and built an immutable
   [`FeedbackEvent`](../concepts/feedback-events.md). The default correlator
   grouped it by its thread, `support-7`, and the event was stored as
   `RECEIVED`, published to subscribers, and offered to the router.
2. **`acknowledge`, `mark_handled`, and `resolve`** moved it through the
   [lifecycle](../concepts/lifecycle.md). Each call checks that the move is
   legal and applies it atomically; calling it twice is harmless.
3. **`query`** returned every event with that correlation ID, oldest first.

Every collaborator of `FeedbackManager()` has a default: an in-memory store, a
router without rules, the run/thread/checkpoint correlator, and a sink that
writes one structured log record per lifecycle event. Nothing runs in the
background.

## Where to go next

- Connect it to your agent:
  [record LangChain failures](../how-to/langchain-failures.md) and
  [human-in-the-loop decisions](../how-to/langgraph-hitl.md).
- Keep feedback in your database: [configuration](configuration.md) and
  [custom stores](../how-to/custom-store.md).
- Learn the model: [concepts overview](../concepts/index.md).
