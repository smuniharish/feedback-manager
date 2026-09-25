# Handlers

Handlers are where application side effects live.

Contract:

```python
class FeedbackHandler(ABC):
    async def handle(
        self, feedback: FeedbackEvent, context: FeedbackContext
    ) -> FeedbackHandlerResult: ...
```

Bundled handler:

- `AuditFeedbackHandler` — structured logging

Typical custom handler uses:

- create a review task
- notify an operator
- enqueue work downstream
- persist feedback into another system

Handler failures are isolated by `FailurePolicy` unless you opt into blocking behavior.

See [advanced custom handler guide](../advanced/custom-handler.md).

