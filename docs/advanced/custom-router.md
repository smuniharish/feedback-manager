# Custom router

Implement `FeedbackRouter` when predicate-based rules are not enough.

```python
from collections.abc import Sequence

from feedback_manager.contracts import FeedbackHandler, FeedbackRouter
from feedback_manager.core import FeedbackEvent


class SeverityRouter(FeedbackRouter):
    def __init__(self, critical: FeedbackHandler, normal: FeedbackHandler) -> None:
        self.critical = critical
        self.normal = normal

    async def route(self, feedback: FeedbackEvent) -> Sequence[FeedbackHandler]:
        score = feedback.payload.get("score")
        if isinstance(score, float) and score < 0.5:
            return [self.critical]
        return [self.normal]
```

The router should be side-effect free; it selects handlers but does not call them.

