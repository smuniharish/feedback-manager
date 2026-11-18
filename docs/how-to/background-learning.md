# Background learning and recovery

Submitting feedback alone does not create a lesson. The host authenticates the
tenant, namespace and agent, independently assesses feedback against an archived
experience and supporting evidence, and records a separate acceptance decision.
Configure an `AssessmentVerifier` on the `LearningEngine`, then explicitly call
`learner.enqueue_assessment(feedback_id, assessment_id, assessment_version=version)`.
The verifier checks the current source snapshots again when the job runs.
Applications can inject a `SignalExtractor` into the verifier; the worker uses
that extractor by default, so verification recomputes the same signal.

The result is a candidate, not an active instruction. A person must separately
review and activate it. The [manual example](basic-learning.md) shows this flow
with synthetic data; the runnable durable example below shows background jobs.

## Memory versus durable operation

The defaults keep feedback, experiences, assessments, jobs and artifacts in
memory. They do not survive process exit. Configure all durability boundaries:

1. A durable `FeedbackStore` with a `FeedbackChangeSource` journal committed in
   the **same transaction** as every feedback write and lifecycle transition.
2. A durable `LearningJobStore`, including replay checkpoints.
3. A durable `ArtifactRepository` with atomic version updates.
4. Durable experience and assessment repositories, supplied to the verifier.

`require_durable_learning=True` rejects incomplete configurations. The complete
single-host example supplies SQLite feedback and artifact adapters and
`SQLiteLearningJobStore`, `SQLiteExperienceRepository` and
`SQLiteAssessmentRepository`. SQLite is not a new mandatory dependency or default
file: these databases are created only when the application explicitly selects
them. The feedback/structured-adapter examples are application code, not a
distributed database service.

## PostgreSQL stores

For several processes or machines, install `feedback-manager[postgres]` and use
`PostgresExperienceRepository`, `PostgresAssessmentRepository` and
`PostgresPayloadStore` from `feedback_manager.storage.postgres`. `SQLitePayloadStore`
is the single-host durable payload archive.

```python
from psycopg_pool import AsyncConnectionPool

from feedback_manager.storage.postgres import (
    PostgresAssessmentRepository,
    PostgresExperienceRepository,
    PostgresPayloadStore,
)

pool = AsyncConnectionPool(dsn, open=False, kwargs={"autocommit": True})
await pool.open(wait=True)
experiences = await PostgresExperienceRepository.open(pool)
assessments = await PostgresAssessmentRepository.open(pool)
payloads = await PostgresPayloadStore.open(pool, codec=my_encryption_codec)
```

- Pass your own `AsyncConnectionPool` to share it; the stores never close it. Pass
  a DSN instead for a store-owned pool that `close()` releases.
- `open` creates the tables idempotently under an advisory lock. Tables are named
  `<table_prefix><name>`; the prefix defaults to `fm_` and must match
  `[a-z_][a-z0-9_]{0,39}`.
- Writes run in one transaction with row locks, so compare-and-set holds across
  processes. Decision inserts are serialized per table prefix, so the decision
  feed's order is commit order.
- Database errors are raised as the store's error type, naming only the
  exception class.
- psycopg's async mode needs a selector event loop, which is not the Windows default.

The `PayloadStoreContract`, `ExperienceRepositoryContract` and
`AssessmentRepositoryContract` kits certify all three stores against a real
database in CI.

## Purging an experience

`ExperiencePurger` removes an experience, its assessments and decisions, and
every payload they reference, within one scope:

```python
from feedback_manager.storage.purge import ExperiencePurger

purger = ExperiencePurger(experiences, payloads, assessments=assessments, engine=engine)
report = await purger.purge(scope, experience_id)
```

When an engine is supplied, it first calls `invalidate_feedback` for each
feedback linked to the experience. That marks the feedback's evidence invalid,
returns affected active artifacts to review and drops them from retrieval; it
does not rewrite artifacts. The experience record is deleted last,
so a purge interrupted by a crash or failure can be run again to finish; the
report counts only what that run deleted. Feedback is never deleted; it stays
authoritative. Payloads must not be shared between experiences. Invalidation raises
`ArtifactConflictError` for an artifact that is already rejected or superseded,
and the purge then stops before deleting anything. The purge spans several
stores and is not one distributed transaction.

```python
--8<-- "examples/15_background_learning.py"
```

Run from the checkout:

```bash
uv sync --locked --all-extras --group examples
uv run --no-sync python examples/15_background_learning.py
```

The example seeds synthetic feedback, simulates a separate reviewer, verifies
RetrievalLoom-backed retrieval and checks that another agent gets no private
artifact. Set `FEEDBACK_MANAGER_STATE_DIR` to retain the databases and run it
twice to check restart persistence. Without it, temporary databases are removed
on exit.

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
- `recover_assessments(assessments)` does the same for accepted assessment
  decisions. It reads the repository's commit-ordered `decision_changes()` feed
  and enqueues one job per accepted decision, under its own checkpoint. Call it at
  startup to recover a crash between recording a decision and enqueueing its job.
  Rejected decisions are skipped; enqueueing the same assessment version is idempotent.
- A configured change source is polled by the worker. Enter the manager's async
  context at startup to recover even if no new feedback arrives.
- `reconcile()` is a convenience scan over current feedback. It is **not** a
  replacement for a durable ordered journal, and cannot reconstruct every missed
  historical transition from a store without a change feed.

Use one worker loop per learner. Stop it before closing application-owned stores.
For cancellation, interrupted jobs remain leased until expiry; graceful stop
finishes the current batch. Bound shutdown externally if a provider can hang.

## Exact guarantees and limits

Feedback-change replay and accepted-decision replay are at least once with a
durable source, assessment repository and queue. There is no
distributed transaction across feedback, artifacts and RetrievalLoom.
RetrievalLoom projections are idempotent and can be repaired with
`RetrievalLoomRetriever.reindex`.
For atomic **artifact version plus projection intent** publication, select a
repository with `atomic_projection_intents=True` and construct
`LearningEngine(repository, atomic_publication=True)`. Unsupported providers fail
explicitly. The bundled in-memory artifact repository does not provide durable
projection intents.

For repositories with an atomic outbox, the host runs
`RetrievalLoomRetriever.drain_intents(scope)` at startup and periodically, using
one serialized projector per tenant/namespace. Each bounded page reads current
authoritative versions, refreshes the RetrievalLoom projection and only then
acknowledges the intent. Failures leave intents pending. This is a local
transactional outbox, **not** a distributed transaction. Stale projections are
rejected during retrieval rather than trusted.

Use one learning writer per tenant/namespace when synchronizing feedback
invalidation: the default engine does not provide a distributed tombstone
transaction spanning all affected artifacts. Publication, correction processing
and cross-process review must be coordinated by the host. These constraints do
not make a feedback store without a change feed crash-recoverable.

## Multiple processes and multiple hosts

On one host, `SQLiteLearningJobStore` leases make claims safe across worker
processes sharing its file. Artifact merges use compare-and-set: concurrent
workers that keep conflicting on the same artifact fail the job transiently, and
it is retried. Feedback Manager ships no networked job store. For workers on
several hosts, implement `LearningJobStore` for your database and run
`LearningJobStoreContract` against it. The repository's
`examples/postgres_learning_job_store.py` shows one PostgreSQL implementation
using `FOR UPDATE SKIP LOCKED` claims.

Each process's owned RetrievalLoom projection is local. If another process
drains the shared projection outbox, this process catches up after
`projection_ttl`, as described in
[Retrieval with RetrievalLoom](hybrid-retrieval.md#scope-and-evidence).

Operational guidance:

- Set `lease` well above your slowest job. A job that outlives its lease is
  reclaimed by another worker. Stale completions are fenced and counted in
  `LearningWorkerStats.lease_lost`; the worker keeps running.
- Learning writes are idempotent, but a reclaimed job repeats its work.
- Throughput depends on the storage topology. The SQLite job store is limited by
  its single writer, so load-test your own stores and deployment before sizing
  worker counts.
