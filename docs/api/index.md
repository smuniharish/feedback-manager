# API reference

The complete public API, generated from the source code's docstrings.

| Module | Contents |
|---|---|
| [`feedback_manager`](manager.md) | `FeedbackManager`, its delivery handles, and the most used names below. |
| [`feedback_manager.core`](models.md) | The domain model: events, targets, sources, categories, statuses, and the lifecycle. |
| [`feedback_manager.contracts`](contracts.md) | The extension contracts, `FeedbackQuery`, and the bundled store and correlator. |
| [`feedback_manager.routing`](routing.md) and [`.handlers`](routing.md#bundled-handlers) | Rule-based routing and the audit handler. |
| [`feedback_manager.policies`](policies.md) | Failure isolation and retention. |
| [`feedback_manager.observability`](observability.md) | Observability events and sinks. |
| [`feedback_manager.integrations`](integrations.md) | LangChain, LangGraph, and `langgraph-xai` integrations. |
| [`feedback_manager.errors`](errors.md) | The exception hierarchy. |

The top-level package re-exports what most applications need:

```python
from feedback_manager import (
    ExecutionContext,
    FeedbackCategory,
    FeedbackEvent,
    FeedbackManager,
    FeedbackProvenanceReference,
    FeedbackQuery,
    FeedbackSource,
    FeedbackStatus,
    FeedbackStream,
    FeedbackTarget,
    FeedbackTargetType,
    Subscription,
    validate_transition,
)
```

along with every exception class and `__version__`. Names not listed in these
pages, including modules and attributes whose names start with an underscore,
are internal and may change without notice.
