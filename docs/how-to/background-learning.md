# Automatic background learning and recovery

```python
from feedback_manager import ExecutionContext, FeedbackManager, FeedbackTarget, FeedbackTargetType

async with FeedbackManager() as manager:
    await manager.for_tenant("acme").submit(
        source="human",
        category="correction",
        target=FeedbackTarget(type=FeedbackTargetType.RUN, id="run-1"),
        payload={"correction": "Validate tool results before acting."},
        execution_context=ExecutionContext(agent_id="researcher", run_id="run-1"),
    )
    await manager.background_learner.drain()
```

The host must authenticate `"acme"`; the tenant facade is isolation, not login.
This creates a candidate, not an active instruction. To publish, aggregate
independent evidence and explicitly review and activate with the learning engine.
The [manual example](basic-learning.md) shows those calls.

## Memory versus durable operation

The defaults keep feedback, jobs and artifact versions in memory. They do not
survive process exit. Configure all three durability boundaries explicitly:

1. A durable `FeedbackStore` with a `FeedbackChangeSource` journal committed in
   the **same transaction** as every feedback write and lifecycle transition.
2. A durable `LearningJobStore`, including replay checkpoints.
3. A durable `ArtifactRepository` with atomic version updates.

`require_durable_learning=True` rejects incomplete configurations. The complete
single-host example supplies SQLite feedback and artifact adapters and
`SQLiteLearningJobStore`. SQLite is not a new mandatory dependency or default
file: these databases are created only when the application explicitly selects
them. The feedback/structured-adapter examples are application code, not a
distributed database service.

```python
--8<-- "examples/15_background_learning.py"
```

Run from the checkout:

```bash
uv sync --locked --all-extras --group examples
uv run --no-sync python examples/15_background_learning.py
```

The example seeds synthetic feedback, simulates a separate reviewer, verifies
hybrid retrieval and checks that another agent gets no private artifact. Set
`FEEDBACK_MANAGER_STATE_DIR` to retain the databases and run it twice to check
restart persistence. Without it, temporary databases are removed on exit.

## Retry and recovery controls

- `run_once()` processes a bounded batch; `drain()` processes currently due jobs.
  Neither waits for delayed retries.
- `RetryPolicy` bounds attempts and exponential backoff with capped jitter.
  Permanent validation/scope errors are dead-lettered immediately.
- `replay_dead_letters()` explicitly requeues parked jobs. Error records contain
  exception types, not feedback text or exception messages.
- Leases recover abandoned jobs. Monotonic attempt counters fence stale
  acknowledgments, including claims reclaimed by another process.
- `recover()` reads durable changes and saves a checkpoint only after all jobs in
  a page have been persisted. A crash before checkpointing safely replays duplicates.
- A configured change source is polled by the worker. Enter the manager's async
  context at startup to recover even if no new feedback arrives.
- `reconcile()` is a convenience scan over current feedback. It is **not** a
  replacement for a durable ordered journal, and cannot reconstruct every missed
  historical transition from a store without a change feed.

Use one worker loop per learner. Stop it before closing application-owned stores.
For cancellation, interrupted jobs remain leased until expiry; graceful stop
finishes the current batch. Bound shutdown externally if a provider can hang.

## Exact guarantees and limits

Delivery is at least once with durable source, queue and artifacts. There is no
distributed transaction across feedback, artifacts, LanceDB and LadybugDB.
Projection writes are idempotent and can be repaired with `ArtifactIndexer.reindex`.
For atomic **artifact version plus projection intent** publication, select a
repository with `atomic_projection_intents=True` and construct
`LearningEngine(repository, atomic_publication=True)`. Unsupported providers fail
explicitly. The SQLite artifact example commits both in one transaction.

The host runs `ArtifactIndexer.drain_intents(scope)` at startup and periodically,
using one serialized projector per tenant/namespace. Each bounded page reads
current authoritative versions, refreshes every configured projection and only
then acknowledges the intent. Failures leave intents pending, including a crash
after a vector write but before a graph write. Coordinate publication and
reindexing with that same writer. This is a local transactional outbox, **not**
atomicity across vector and graph databases. Stale projections are rejected
during retrieval rather than trusted.

Use one learning writer per tenant/namespace when synchronizing feedback
invalidation: the default engine does not provide a distributed tombstone
transaction spanning all affected artifacts. Publication, correction processing
and cross-process review must be coordinated by the host. These constraints do
not make a feedback store without a change feed crash-recoverable.
