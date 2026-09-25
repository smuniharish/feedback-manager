# Quickstart

This example uses the real public API exposed by `feedback_manager.__init__`.

```python
import asyncio

from feedback_manager import (
    FeedbackCategory,
    FeedbackManager,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.core.context import ExecutionContext


async def main() -> None:
    manager = FeedbackManager()

    feedback = await manager.submit(
        source=FeedbackSource.HUMAN,
        category=FeedbackCategory.CORRECTION,
        target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id="gen-42"),
        payload={
            "original_text": "The capital of Australia is Sydney.",
            "corrected_text": "The capital of Australia is Canberra.",
        },
        execution_context=ExecutionContext(generation_id="gen-42"),
    )

    await manager.acknowledge(feedback.feedback_id)
    await manager.mark_handled(feedback.feedback_id)
    resolved = await manager.resolve(
        feedback.feedback_id,
        resolution={"applied": True, "channel": "manual_review"},
    )

    print(resolved.status)
    print(resolved.metadata["resolution"])


asyncio.run(main())
```

Expected output:

```text
resolved
{'applied': True, 'channel': 'manual_review'}
```

## What happened?

1. `submit()` created a `FeedbackEvent`
2. the manager correlated and persisted it
3. the event moved to `RECEIVED`
4. the application acknowledged and handled it
5. `resolve()` completed the lifecycle and stored resolution metadata

For the underlying domain rules, see [Lifecycle](../concepts/lifecycle.md) and the deeper [architecture document](../architecture/LIFECYCLE.md).

