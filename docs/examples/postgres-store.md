# Example: a real PostgreSQL-backed `FeedbackStore`

Source file: `examples/postgres_feedback_store.py`

`feedback-manager` ships only an in-memory reference `FeedbackStore` (Section 6 of the design explicitly excludes a bundled "custom database/ORM"). This example shows what a production store looks like: a real, async, JSONB-backed `FeedbackStore` implementation on top of `psycopg` 3, tested against a real PostgreSQL 16 instance (here, run via Podman).

Key pattern -- overriding the default `store=`:

```python
from feedback_manager import FeedbackManager
from postgres_feedback_store import PostgresFeedbackStore

store = await PostgresFeedbackStore.connect(
    "postgresql://feedback:feedback@<host>:5432/feedback_manager"
)
manager = FeedbackManager(store=store)
```

The store implements the same `create`/`get`/`update`/`transition`/`query`/`list` contract as `InMemoryFeedbackStore`, persisting each `FeedbackEvent` as JSONB plus an indexed `status` column, and enforcing idempotency with a `UNIQUE` constraint.

!!! note "Windows + psycopg async"
    `psycopg`'s async mode does not support Windows' default `ProactorEventLoop`. On Windows the script runs under `asyncio.run(..., loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))`.

## Real run, real database

A PostgreSQL 16 (alpine) container was started with Podman:

```console
$ podman run -d --name fm-postgres -e POSTGRES_USER=feedback -e POSTGRES_PASSWORD=feedback \
    -e POSTGRES_DB=feedback_manager -p 5432:5432 postgres:16-alpine
```

Running the smoke test (`uv run python examples/postgres_feedback_store.py`) submits feedback, walks it through `acknowledge -> mark_handled -> resolve`, and queries it back -- against the real container, not a mock:

```
Submitted: id=... status=received
Fetched back: status=received
Acknowledged -> Handled -> Resolved: status=resolved
Query by status=resolved: 1 event(s)
```

Verifying directly against the database (bypassing the package entirely) confirms the row is really there:

```console
$ podman exec fm-postgres psql -U feedback -d feedback_manager \
    -c "SELECT feedback_id, status FROM feedback_events;"
              feedback_id              |  status
--------------------------------------+----------
 0489b633-2c8b-4f61-93c4-3e5f53be169b | resolved
```
