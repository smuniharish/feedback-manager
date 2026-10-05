# Store feedback in your database

The in-memory default loses feedback when the process exits. To keep it, give
`FeedbackManager` a store backed by your database: subclass `FeedbackStore`
and implement four async methods.

```python
manager = FeedbackManager(store=SQLiteFeedbackStore("feedback.db"))
```

This guide builds a complete store on SQLite, from the Python standard
library. The same design carries over to any database; the
[PostgreSQL store](../examples/production.md#postgresql-store) applies it with a
connection pool and SQL-side filtering.

## The four methods

| Method | Requirement | In SQL |
|---|---|---|
| `create(feedback)` | Insert the event. When its `idempotency_key` is already stored, return the stored event instead. A duplicate `feedback_id` raises `FeedbackStoreError`. | `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING`, then read the stored event if nothing was inserted. |
| `get(feedback_id)` | Return the event or `None`. | `SELECT ... WHERE feedback_id = ?` |
| `transition(feedback_id, status, *, expected, resolution)` | A compare-and-set: apply the move only if the stored status still equals `expected`. | One transaction, or one `UPDATE ... WHERE feedback_id = ? AND status = ?`. |
| `query(query)` | Return matching events in creation order, newest first when asked, up to `limit`. Look up `idempotency_key` quickly: the manager does so before processing each submission that has a key. | `WHERE` clauses for the filters, `ORDER BY` an insertion sequence, `LIMIT`; a unique index on `idempotency_key`. |

`transition` must also:

1. raise `FeedbackNotFoundError` when the event does not exist;
2. raise `FeedbackConflictError` when the stored status is not `expected`;
3. call `validate_transition(feedback_id, expected, status)`, which raises
   `FeedbackLifecycleError` for an illegal move and reports a same-status move
   as `idempotent`, in which case the stored event is returned unchanged;
4. build the updated event with `current.with_status(status, resolution=resolution)`,
   which moves `updated_at` forward and keeps the existing resolution when
   `resolution` is `None`.

The manager does the rest: it validates input, retries transitions on
conflict, and publishes each change exactly once.

## A complete SQLite store

Store the whole event as JSON, next to the columns you need to look it up.
`model_dump_json` and `model_validate_json` round-trip events without loss.

??? example "examples/sqlite_feedback_store.py"

    ```python
    --8<-- "examples/sqlite_feedback_store.py"
    ```

Key decisions in this implementation:

- **Atomic transitions.** `BEGIN IMMEDIATE` takes SQLite's write lock before
  reading the current status, so no other writer can change it before the
  update. In a database with row locks, a single conditional
  `UPDATE ... WHERE status = expected` gives the same guarantee without a
  transaction.
- **Non-blocking.** Each blocking call runs in a worker thread with
  `asyncio.to_thread`, so the event loop keeps serving other tasks.
- **Creation order.** An auto-incrementing `position` column records insertion
  order, which `query` sorts by.
- **Simple queries.** `query.matches(event)` applies every filter in Python.
  That is correct for any store but reads every row, so the idempotency-key
  lookup goes straight to the unique index instead. Translate the other
  filters to `WHERE` clauses, as the PostgreSQL store does, once you have many
  events.

Run its self-check:

```bash
uv run python examples/sqlite_feedback_store.py
```

```text
Stored 8734043d-54c1-4214-8241-e8016a8103a8 (received) in feedback.db
Idempotent retry returned the same event: True
After reopening: resolved, resolution={'reviewed': True}
Ratings stored: 1
```

## Test your store

Run the same checks the bundled stores pass: create and duplicates,
concurrent idempotent creates, compare-and-set transitions, resolutions,
queries compared against `InMemoryFeedbackStore`, and concurrent lifecycle
calls through a manager. The repository's
[store contract tests](https://github.com/smuniharish/feedback-manager/blob/master/tests/examples/test_stores.py)
run them against both example stores; add your store to their fixture or copy
them into your project.

## Errors

Raise feedback-manager's own errors for contract outcomes (not found, conflict,
illegal move, duplicate ID). Anything else your store raises, such as a lost
connection, reaches the caller as `FeedbackStoreError` with the original
exception chained, and creates or transitions that fail emit `feedback.failed`.
