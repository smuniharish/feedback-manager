# Custom store

Implement `FeedbackStore` when you need durable persistence.

Required methods:

```python
from collections.abc import Sequence
from uuid import UUID

from feedback_manager.contracts import FeedbackQuery, FeedbackStore
from feedback_manager.core import FeedbackEvent, FeedbackStatus


class MyStore(FeedbackStore):
    async def create(self, feedback: FeedbackEvent) -> FeedbackEvent: ...
    async def get(self, feedback_id: UUID) -> FeedbackEvent | None: ...
    async def update(self, feedback: FeedbackEvent) -> FeedbackEvent: ...
    async def transition(self, feedback_id: UUID, status: FeedbackStatus) -> FeedbackEvent: ...
    async def query(self, query: FeedbackQuery) -> Sequence[FeedbackEvent]: ...
    async def list(self) -> Sequence[FeedbackEvent]: ...
```

Important behavioral requirements from the contract docstring:

- safe under concurrent use
- `create()` must honor `idempotency_key`
- `transition()` must validate lifecycle transitions

The bundled `InMemoryFeedbackStore` is the reference implementation to follow.

