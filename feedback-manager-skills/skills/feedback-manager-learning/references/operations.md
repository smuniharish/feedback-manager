# Operations: durability, retries, recovery

## Contents

- [Memory versus durable](#memory-versus-durable)
- [A durable single-host setup](#a-durable-single-host-setup)
- [Running the worker](#running-the-worker)
- [Retries and dead letters](#retries-and-dead-letters)
- [Recovery after a crash](#recovery-after-a-crash)
- [Guarantees and limits](#guarantees-and-limits)
- [Monitoring](#monitoring)

## Memory versus durable

`FeedbackManager()` learns by default, but keeps feedback, learning jobs, and
artifacts in memory; all are lost on exit. A durable deployment needs all
three:

1. A durable `FeedbackStore` that is also a `FeedbackChangeSource`, writing a
   change journal in the same transaction as every feedback write and
   lifecycle move. Pass a separate source with `learning_change_source=` if
   the store is not one.
2. A durable `LearningJobStore` (`durable = True`), such as
   `SQLiteLearningJobStore(path)`.
3. A durable `ArtifactRepository` (`durable = True`) with atomic version
   updates.

`FeedbackManager(..., require_durable_learning=True)` raises
`UnsupportedCapabilityError` when any of the three is missing. Use it in
production so a misconfiguration fails at startup, not after a crash.

## A durable single-host setup

```python
from feedback_manager import FeedbackManager
from feedback_manager.learning.engine import LearningEngine
from feedback_manager.learning.jobs import SQLiteLearningJobStore

repository = SQLiteArtifactRepository(state / "artifacts.db")  # your durable repository
jobs = SQLiteLearningJobStore(state / "learning-jobs.db")
engine = LearningEngine(repository, atomic_publication=True)
manager = FeedbackManager(
    store=SQLiteFeedbackStore(state / "feedback.db"),  # your durable store
    learning_engine=engine,
    learning_job_store=jobs,
    require_durable_learning=True,
)
```

`SQLiteFeedbackStore` and `SQLiteArtifactRepository` are complete, tested
implementations in the repository's
[examples](https://github.com/smuniharish/feedback-manager/tree/master/examples);
copy and adapt them, or write your own with the feedback-manager-extend skill.

## Running the worker

- The worker starts by itself when feedback is enqueued. With a change
  source, `async with manager:` also starts it at startup to recover work
  even if no new feedback arrives. Leaving the block, or
  `await manager.aclose()`, stops it and drains due jobs; delayed retries stay
  queued for the next start.
- `manager.background_learner.start(subscribe=True, reconcile=False)` and
  `stop(cancel=False)` control it directly. A graceful stop finishes the
  current batch.
- `run_once()` processes one bounded batch; `drain(max_batches=1000)`
  processes every job that is due now. Use them in tests, scripts, and cron
  jobs. Neither waits for delayed retries.
- Run one worker loop per learner, and stop it before closing stores your
  application owns.

## Retries and dead letters

```python
from datetime import timedelta

from feedback_manager.learning import BackgroundLearner, RetryPolicy

RetryPolicy(
    max_attempts=5,
    initial_delay=timedelta(seconds=1),
    max_delay=timedelta(seconds=300),
    multiplier=2.0,
    jitter=0.1,
)  # the defaults
```

To change it, build a `BackgroundLearner(manager, engine, jobs, scope_resolver=..., retry=...)`
yourself with `FeedbackManager(learning=False)`.

- Transient errors retry with capped exponential backoff and jitter.
- Validation and scope errors are permanent and go straight to
  `dead_lettered`.
- Inspect them with `await jobs.list(status=LearningJobStatus.DEAD_LETTERED)`
  and `await jobs.counts()`. Error records hold exception types, not feedback
  text.
- After fixing the cause, `await learner.replay_dead_letters(limit=100)`
  requeues them.

Job statuses: `pending`, `running`, `succeeded`, `dead_lettered`.

## Recovery after a crash

- Leases recover jobs abandoned by a crashed worker. Attempt counters fence
  late acknowledgments from a worker that lost its lease.
- `recover()` replays the durable change feed from the last checkpoint. It
  saves the checkpoint only after every job of a page is stored, so a crash
  replays duplicates, which are deduplicated by job ID.
- `reconcile(query)` scans current feedback and enqueues what is missing. It
  is a convenience, not a substitute for a change feed: it cannot see
  transitions that are already gone.

## Guarantees and limits

- Delivery is at least once when the feedback source, job store, and
  artifact repository are all durable.
- There is no distributed transaction across feedback, artifacts, and the
  RetrievalLoom projection. With `atomic_publication=True` and a repository
  that supports projection intents, the artifact change and its projection
  intent commit together. `RetrievalLoomRetriever.drain_intents(scope)` applies
  and acknowledges a bounded page; retrieval also drains intents before
  searching. This does not make the retrieval index authoritative or durable.
- An owned retriever's projection is process-local. Over a durable repository
  shared by several processes, it rebuilds each scope after `projection_ttl`
  (default 30 seconds), so another process's newly activated artifact can lag by
  up to that long. Stale or deprecated artifacts are never returned, because
  every hit is revalidated.
- `SQLiteLearningJobStore` is safe across processes on one host only. Workers
  on several hosts need a networked `LearningJobStore` certified with
  `LearningJobStoreContract`.
- The legacy LanceDB and LadybugDB adapters are compatibility paths, not the
  default retrieval engine.
- Use one learning writer per tenant and namespace for review, activation,
  and `invalidate_feedback`.

## Monitoring

Learning operations (`learning.batch`, `learning.job`,
`learning.projection`, `learning.retrieval`) report to an `OperationObserver`.
`OpenTelemetryObservabilitySink` is both a sink and an observer: pass it as
`observability_sink=` to the manager and `observer=` to `LearningEngine` or
`RetrievalLoomRetriever`; background jobs use the engine's observer. The legacy
`ArtifactIndexer` and retrievers also accept an observer. Alert on growing
`dead_lettered` counts and on `pending` jobs older than a few lease periods.
A rising `learner.stats.lease_lost` means jobs outlive their lease and are
redone; lengthen the lease.
