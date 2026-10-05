# Route feedback to handlers

This guide sends low evaluator scores to a human review queue, then works
through the queue. It follows
[example 05](../examples/basics.md#05-evaluator-feedback).

## 1. Write the handler

A handler reacts to one new event and reports the outcome:

```python
from feedback_manager import FeedbackEvent
from feedback_manager.contracts import FeedbackHandler, FeedbackHandlerResult


class ReviewQueue(FeedbackHandler):
    """Collects feedback that needs a person's attention."""

    def __init__(self) -> None:
        self.pending: list[str] = []

    async def handle(self, feedback: FeedbackEvent) -> FeedbackHandlerResult:
        self.pending.append(feedback.target.id)
        return FeedbackHandlerResult(handled=True, detail="queued for human review")
```

In production, `handle` would insert into your work queue or ticketing system.
Keep it short: handlers run in sequence before `submit` returns.

## 2. Decide what to route

A predicate is a function from `FeedbackEvent` to `bool`. Payload values are
arbitrary JSON, so check types before comparing:

```python
def low_score(feedback: FeedbackEvent) -> bool:
    score = feedback.payload.get("score")
    return isinstance(score, float) and score < 0.5
```

Combine predicates with `all_of` and `any_of`, and use `by_source`,
`by_category`, and `by_target_type` for the common cases.

## 3. Wire the router

```python
from feedback_manager import FeedbackManager
from feedback_manager.routing import DefaultFeedbackRouter, RoutingRule

queue = ReviewQueue()
router = DefaultFeedbackRouter([RoutingRule(predicate=low_score, handlers=[queue])])
manager = FeedbackManager(router=router)
```

Submitting three evaluator verdicts routes only the low one:

```text
gen-1: score=0.95 (Correct.)
gen-2: score=0.2 (Names the wrong city.)
gen-3: score=0.95 (Correct.)
Routed to human review: ['gen-2']
```

## 4. Work through the queue

Routing hands feedback over; the [lifecycle](../concepts/lifecycle.md) records
what happens next. A reviewer acknowledges an item when they pick it up, marks
it handled when the fix is in, and resolves it:

```python
await manager.acknowledge(feedback_id)
await manager.mark_handled(feedback_id)
await manager.resolve(feedback_id, resolution={"fixed_in": "prompt-v12"})
```

A handler may also move the event itself, for example acknowledging it when it
creates a ticket. `submit` returns the event as it was stored (`RECEIVED`); use
`manager.get` to read its latest state.

## Patterns

- **Catch-all handlers.** `DefaultFeedbackRouter(rules, default_handlers=[...])`
  runs the default handlers when no rule matches, for example
  `AuditFeedbackHandler` to log everything else.
- **Rules at runtime.** `router.add_rule(rule)` appends a rule, for example
  when a tenant enables a feature.
- **Deliberate no-ops.** Return `FeedbackHandlerResult(handled=False)` when a
  handler decides an event is not for it. The `feedback.routed` event counts
  handlers selected (`handler_count`) and handled (`handled_count`).
- **Failures.** A raising handler does not stop the next one. Make failures
  raise instead with a `BLOCKING` [handler failure mode](failure-isolation.md).
- **Lifecycle changes.** Handlers see new feedback only. React to status
  changes with a [subscriber](../concepts/delivery.md).
