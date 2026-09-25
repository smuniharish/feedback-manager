# Custom handler

Implement `FeedbackHandler` when feedback should trigger application-specific side effects.

```python
from feedback_manager.contracts import FeedbackContext, FeedbackHandler, FeedbackHandlerResult
from feedback_manager import FeedbackEvent


class HumanReviewHandler(FeedbackHandler):
    async def handle(
        self, feedback: FeedbackEvent, context: FeedbackContext
    ) -> FeedbackHandlerResult:
        # create ticket, enqueue job, notify operator, etc.
        return FeedbackHandlerResult(handled=True, detail="queued for review")
```

Handler failures are isolated by default because `FeedbackManager` runs them through `FailurePolicy` as stage `HANDLER`.
