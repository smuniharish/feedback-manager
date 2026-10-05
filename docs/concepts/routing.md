# Routing and handlers

Handlers are your code that reacts to new feedback: open a ticket, queue a
correction for review, notify a channel, update an evaluation dataset. The
router decides which handlers see each new event; the manager runs them.

## Handlers

A handler implements one method and reports what it did:

```python
from feedback_manager import FeedbackEvent
from feedback_manager.contracts import FeedbackHandler, FeedbackHandlerResult


class ReviewQueue(FeedbackHandler):
    def __init__(self) -> None:
        self.pending: list[FeedbackEvent] = []

    async def handle(self, feedback: FeedbackEvent) -> FeedbackHandlerResult:
        self.pending.append(feedback)
        return FeedbackHandlerResult(handled=True, detail="queued for review")
```

Return `FeedbackHandlerResult(handled=False)` when the handler deliberately
does nothing; that is not a failure. Raise only for genuine failures: each
handler's failure is isolated according to the `HANDLER`
[failure mode](../how-to/failure-isolation.md), so one failing handler does not
stop the others. A handler that returns anything other than a
`FeedbackHandlerResult` fails the same way.

`AuditFeedbackHandler` is bundled: it writes one structured log record per
event, without the payload.

## The default router

`DefaultFeedbackRouter` evaluates an ordered list of rules. Each `RoutingRule`
pairs a predicate with the handlers to run when it matches:

```python
from feedback_manager import FeedbackEvent, FeedbackManager
from feedback_manager.handlers import AuditFeedbackHandler
from feedback_manager.routing import (
    DefaultFeedbackRouter,
    RoutingRule,
    all_of,
    by_category,
    by_source,
)


def low_score(feedback: FeedbackEvent) -> bool:
    score = feedback.payload.get("score")
    return isinstance(score, float) and score < 0.5


queue = ReviewQueue()
router = DefaultFeedbackRouter(
    [
        RoutingRule(by_category("correction"), [queue], name="corrections"),
        RoutingRule(all_of(by_source("evaluator"), low_score), [queue], name="low scores"),
    ],
    default_handlers=[AuditFeedbackHandler()],
)
manager = FeedbackManager(router=router)
```

- Every matching rule contributes its handlers, in rule order. A handler that
  several rules select runs once.
- When no rule matches, the `default_handlers` run.
- `router.add_rule(rule)` appends a rule at runtime.
- A bare `FeedbackManager()` has no rules and no default handlers: feedback is
  stored and published, and no handler runs.

Predicates are plain functions from `FeedbackEvent` to `bool`. The helpers
`by_source`, `by_category`, `by_target_type`, `any_of`, and `all_of` cover the
common cases; any callable works.

## When routing happens

Routing runs once per submission, right after the event is stored and
published. Lifecycle changes are not routed: to react to them, use a
[subscriber or stream](delivery.md). An idempotent resubmission returns the
stored event without routing it again.

After the handlers ran, the manager emits `feedback.routed` with the number of
handlers selected (`handler_count`) and how many reported `handled=True`
(`handled_count`).

## A custom router

Implement `FeedbackRouter` to route on anything, such as a per-tenant table.
A router only selects handlers; it must not invoke them.

```python
from collections.abc import Sequence

from feedback_manager import FeedbackEvent
from feedback_manager.contracts import FeedbackHandler, FeedbackRouter


class TenantRouter(FeedbackRouter):
    def __init__(self, handlers_by_tenant: dict[str, list[FeedbackHandler]]) -> None:
        self._handlers = handlers_by_tenant

    async def route(self, feedback: FeedbackEvent) -> Sequence[FeedbackHandler]:
        context = feedback.execution_context
        tenant = context.tenant_id if context is not None else None
        return self._handlers.get(tenant or "", [])
```

[Routing feedback to handlers](../how-to/routing.md) builds a review queue end
to end.
