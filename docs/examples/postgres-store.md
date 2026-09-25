# Example: a real PostgreSQL-backed `FeedbackStore`

`feedback-manager` ships only an in-memory reference `FeedbackStore`
(Section 6 of the design explicitly excludes a bundled "custom
database/ORM"). This example shows what a production store looks like: a
real, async, JSONB-backed `FeedbackStore` implementation on top of
`psycopg` 3, tested against a real PostgreSQL 16 instance (here, run via
Podman).

The store implements the same `create`/`get`/`update`/`transition`/`query`/`list`
contract as `InMemoryFeedbackStore`, persisting each `FeedbackEvent` as
JSONB plus an indexed `status` column, and enforcing idempotency with a
`UNIQUE` constraint.

Full source, embedded directly from `examples/postgres_feedback_store.py`:

```python title="examples/postgres_feedback_store.py"
--8<-- "examples/postgres_feedback_store.py"
```

!!! note "Windows + psycopg async"
    `psycopg`'s async mode does not support Windows' default `ProactorEventLoop`. On Windows the script runs under `asyncio.run(..., loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))`.

## Real run, real database

A PostgreSQL 16 (alpine) container was started with Podman:

```console
$ podman run -d --name fm-postgres -e POSTGRES_USER=feedback -e POSTGRES_PASSWORD=feedback \
    -e POSTGRES_DB=feedback_manager -p 5432:5432 postgres:16-alpine
```

Running the smoke test submits feedback, walks it through
`acknowledge -> mark_handled -> resolve`, queries it back, and lists the
whole table -- against the real container, not a mock:

```console
$ uv run python examples/postgres_feedback_store.py
Created: a5958710-6bd1-43f4-ad5c-43074b8ec6b3 status=received
Fetched back from Postgres: status=received payload={'rating': 5, 'comment': 'Postgres-backed persistence works'}
Acknowledged: status=acknowledged
Handled: status=handled
Resolved: status=resolved
Query for RESOLVED feedback returned 20 row(s)
Total rows currently in Postgres table: 1614
```

The row counts above are large because this same database has accumulated
feedback from every other Postgres-backed example and matrix run in this
documentation set (see [Grafana observability](grafana-observability.md),
which queries and visualizes that accumulated data). Verifying directly
against the database (bypassing the package entirely) confirms the
just-created row is really there:

```console
$ podman exec fm-postgres psql -U feedback -d feedback_manager \
    -c "SELECT feedback_id, status FROM feedback_events ORDER BY created_at DESC LIMIT 1;"
              feedback_id              |  status
--------------------------------------+----------
 a5958710-6bd1-43f4-ad5c-43074b8ec6b3 | resolved
```
