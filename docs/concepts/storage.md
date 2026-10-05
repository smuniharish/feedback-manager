# Storage and queries

A `FeedbackStore` persists feedback events and answers queries. The manager
talks to the store through four methods, so any database can back it.

## The bundled in-memory store

`InMemoryFeedbackStore` is the default. It is safe to use from many tasks,
threads, and event loops at once, and keeps everything in process memory, so
its contents are lost when the process exits. Use it for development, tests,
and short-lived workers; use a durable store in production.

## Querying feedback

`FeedbackManager.query` takes a `FeedbackQuery`. Every filter is optional, and
all the filters you set must match.

```python
from datetime import UTC, datetime, timedelta

from feedback_manager import FeedbackQuery, FeedbackStatus

recent_corrections = await manager.query(
    FeedbackQuery(
        category="correction",
        status=FeedbackStatus.RECEIVED,
        created_after=datetime.now(UTC) - timedelta(days=1),
        newest_first=True,
        limit=20,
    )
)
everything = await manager.query()
```

| Filter | Matches |
|---|---|
| `source`, `category` | Events with that source or category. |
| `target_type`, `target_id` | Events whose target has that type or ID. |
| `status` | Events in that status; a `FeedbackStatus` or its string value. |
| `correlation_id` | Events with that correlation ID. |
| `idempotency_key` | The event submitted with that idempotency key. |
| `created_after` | Events created at or after that time. |
| `created_before` | Events created strictly before that time. |
| `newest_first` | Returns the newest events first; oldest first by default. |
| `limit` | Returns at most that many events. |

Invalid queries fail when they are built, with `FeedbackValidationError`: an
unknown status, a timezone-naive time, an empty time range, or a `limit` that
is not a positive integer. `manager.get(feedback_id)` returns one event, or
`None`.

## The store contract

| Method | Must |
|---|---|
| `create(feedback)` | Store a new event. If an event with the same `idempotency_key` exists, return that event unchanged instead. Raise `FeedbackStoreError` if the `feedback_id` already exists. |
| `get(feedback_id)` | Return the event, or `None`. |
| `transition(feedback_id, status, *, expected, resolution=None)` | Atomically move the event from `expected` to `status`: validate the move with `validate_transition`, apply it only if the stored status still equals `expected`, and build the updated event with `FeedbackEvent.with_status`. Return the stored event unchanged when `status == expected`. Raise `FeedbackNotFoundError`, `FeedbackConflictError` if the stored status differs, or `FeedbackLifecycleError` for an illegal move. |
| `query(query)` | Return the matching events in creation order, newest first when `query.newest_first` is set, and at most `query.limit` of them. `query.matches(event)` implements the filters. Make the `idempotency_key` lookup fast: `submit` runs it before processing every submission that has a key. |

Implementations must be safe to call concurrently. The compare-and-set in
`transition` is what makes [concurrent lifecycle updates](lifecycle.md#concurrent-updates)
safe: in SQL it is a single `UPDATE ... WHERE feedback_id = ... AND status = ...`.

[Storing feedback in your database](../how-to/custom-store.md) implements the
contract step by step, and the [PostgreSQL store](../examples/production.md#postgresql-store)
is a complete, production-ready implementation.

## Store failures

Feedback that was not stored cannot be processed, so persistence is not an
isolated stage. An exception from the store is raised as `FeedbackStoreError`,
chaining the original exception. Errors that are already feedback-manager
errors pass through unchanged, such as `FeedbackNotFoundError` or
`FeedbackLifecycleError` about the caller's request. When the store fails to
create or transition an event, the manager also emits `feedback.failed` with
`stage="store"`.
