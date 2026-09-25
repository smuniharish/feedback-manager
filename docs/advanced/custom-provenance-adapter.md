# Custom provenance adapter

Implement `FeedbackProvenanceAdapter` if provenance comes from something other than `langgraph-xai`.

```python
from feedback_manager.contracts import FeedbackProvenanceAdapter
from feedback_manager.core import CorrelationContext, FeedbackProvenanceReference


class MyProvenanceAdapter(FeedbackProvenanceAdapter):
    async def resolve(
        self, correlation: CorrelationContext
    ) -> FeedbackProvenanceReference | None:
        return FeedbackProvenanceReference(
            provider="my-system",
            execution_id=correlation.execution.run_id if correlation.execution else None,
            summary="resolved from external provenance service",
        )
```

Then pass it to the manager:

```python
manager = FeedbackManager(provenance_adapter=MyProvenanceAdapter())
```

