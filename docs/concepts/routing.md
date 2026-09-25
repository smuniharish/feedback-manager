# Routing

Routing selects which handlers should process a feedback event.

Bundled pieces:

- `RoutingRule`
- `by_source()`
- `by_category()`
- `by_target_type()`
- `all_of()`
- `any_of()`
- `DefaultFeedbackRouter`

Example:

```python
from feedback_manager.routing import DefaultFeedbackRouter, RoutingRule, by_category
from feedback_manager import FeedbackCategory

router = DefaultFeedbackRouter(
    [
        RoutingRule(
            predicate=by_category(FeedbackCategory.QUALITY),
            handlers=(quality_handler,),
        )
    ]
)
```

The router selects handlers only. `FeedbackManager` invokes them and applies failure isolation.

Deep dive: [Routing architecture](../architecture/ROUTING_MODEL.md).

