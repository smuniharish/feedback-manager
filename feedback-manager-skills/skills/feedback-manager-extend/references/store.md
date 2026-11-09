# Writing a FeedbackStore

## Contents

- [The four methods](#the-four-methods)
- [Table layout](#table-layout)
- [Ordering and queries](#ordering-and-queries)
- [Change source for durable learning](#change-source-for-durable-learning)
- [Contract failures](#contract-failures)

## The four methods

| Method | Must |
|---|---|
| `create(feedback)` | Insert the event. When its `idempotency_key` already exists in the same tenant (or unscoped partition), return the stored event unchanged. Raise `FeedbackStoreError` for a duplicate `feedback_id`. |
| `get(feedback_id)` | Return the event, or `None`. |
| `transition(feedback_id, status, *, expected, resolution=None)` | Atomically: raise `FeedbackNotFoundError` if missing, `FeedbackConflictError` if the stored status is not `expected`; call `validate_transition(feedback_id, expected, status)`; return the stored event unchanged when the move is idempotent; otherwise store and return `current.with_status(status, resolution=resolution)`. |
| `query(query)` | Return events matching every set `FeedbackQuery` filter, in creation order (newest first when `newest_first`), at most `limit`. |

The store must be safe to call concurrently from tasks, threads, and event
loops. Never mutate a `FeedbackEvent`; it is frozen.

## Table layout

Store the whole event as JSON (`model_dump_json`, `model_validate_json`) next
to indexed columns for each filter, plus an insertion sequence:

```sql
CREATE TABLE feedback_events (
    sequence        BIGSERIAL PRIMARY KEY,
    feedback_id     UUID NOT NULL UNIQUE,
    tenant_id       TEXT,
    idempotency_key TEXT,
    source          TEXT NOT NULL,
    category        TEXT NOT NULL,
    target_type     TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    status          TEXT NOT NULL,
    correlation_id  TEXT,
    created_at      TIMESTAMPTZ NOT NULL,
    event           JSONB NOT NULL
);
CREATE UNIQUE INDEX ON feedback_events (COALESCE(tenant_id, ''), idempotency_key)
    WHERE idempotency_key IS NOT NULL;
```

`create` becomes `INSERT ... ON CONFLICT DO NOTHING RETURNING event`, then a
`SELECT` of the existing row when nothing returns. `transition` becomes
`SELECT ... FOR UPDATE` and `UPDATE ... WHERE feedback_id = ? AND status = ?`
in one transaction.

`FeedbackManager.submit` queries by `idempotency_key` before processing, so
keep that lookup indexed.

## Ordering and queries

`FeedbackQuery.matches(event)` is the reference for every filter. Translate
each field: `source`, `category`, `target_type`, `target_id`, `status`,
`correlation_id`, `idempotency_key`, `created_after` (inclusive),
`created_before` (exclusive), `tenant_id`, and `unscoped_only` (records
without a tenant). Apply `limit` after filtering, so a tenant query never
returns another tenant's rows.

Order by the insertion sequence, not by `created_at`: timestamps can tie.

## Change source for durable learning

`require_durable_learning=True` needs a `FeedbackChangeSource`. Subclass both:

```python
from feedback_manager.contracts import FeedbackStore
from feedback_manager.contracts.changes import FeedbackChangePage, FeedbackChangeSource


class PostgresFeedbackStore(FeedbackStore, FeedbackChangeSource):
    async def read_changes(self, *, after: str | None, limit: int) -> FeedbackChangePage:
        # SELECT change_id, event FROM feedback_changes WHERE change_id > :after
        #   ORDER BY change_id LIMIT :limit + 1
        return FeedbackChangePage(events=events, cursor=last_change_id, has_more=more)
```

Write a row to the change journal **in the same transaction** as every
`create` and every applied `transition`. The cursor is an opaque string the
learner stores as its checkpoint. The repository's
[SQLite example](https://github.com/smuniharish/feedback-manager/blob/master/examples/sqlite_feedback_store.py)
implements both.

## Contract failures

| Failing test | Usual cause |
|---|---|
| `test_idempotency_keys_deduplicate_concurrent_creates` | A check-then-insert without a unique index; two concurrent creates both insert. |
| `test_transitions_are_compare_and_set` | The `UPDATE` does not check `status = expected`, or the conflict raises the wrong error. |
| `test_resolutions_are_recorded_once` | A repeated closing move overwrites the resolution instead of returning the stored event. |
| `test_queries_filter_order_and_limit` | A missing filter, `limit` applied before filtering, a `created_before` treated as inclusive, or `newest_first` ignored. |
| `test_duplicate_feedback_ids_are_rejected` | `ON CONFLICT DO NOTHING` on `feedback_id` hides the duplicate; raise `FeedbackStoreError`. |
| `test_create_and_get_round_trip` | A column type loses precision, such as a naive timestamp or a float; store the JSON. |
