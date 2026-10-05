# Reliability

What feedback-manager guarantees, and what it leaves to your infrastructure.

## Guarantees

| Property | Guarantee |
|---|---|
| **Validation** | Invalid input is rejected with `FeedbackValidationError` before anything is stored or published. |
| **Immutability** | Events never change in place; every change produces a new copy. |
| **Single write** | `submit` stores new feedback in one write, already `RECEIVED`. |
| **Idempotent submission** | A submission whose `idempotency_key` is already stored returns the stored event before any processing. Concurrent first submissions with one key store, publish, and route a single event, given a store that enforces the key atomically. |
| **Idempotent transitions** | Moving feedback to the status it is in returns it unchanged and publishes nothing. |
| **Atomic transitions** | Transitions are compare-and-set on the store: concurrent updates never overwrite each other, and a move that became illegal is rejected. |
| **Exactly-once publication** | Each stored change is published to subscribers, streams, and the observability sink once. |
| **Failure isolation** | A failing correlator, provenance lookup, router, handler, subscriber, or sink never loses stored feedback. |
| **Visible failures** | Every stage failure emits `feedback.failed`; every store failure raises `FeedbackStoreError`. |
| **Typed errors** | Every error derives from `FeedbackManagerError` and chains its cause. |

These properties are covered by the test suite, including property-based tests
of the lifecycle state machine and stress tests with many threads and event
loops.

## Concurrency

- A `FeedbackManager` can be shared by many tasks, threads, and event loops.
- `InMemoryFeedbackStore` is safe under the same conditions.
- Streams deliver changes made from any task, thread, or loop to the loop that
  opened them.
- The package starts no background tasks or threads. Subscribers and handlers
  run in the task that made the change, before the call returns.

## Delivery semantics

| Mechanism | Semantics |
|---|---|
| Store | The system of record: durable if your store is. |
| Handlers | Run at most once per submission, in order, without retries. |
| Subscribers and streams | In-process and not durable: changes made while a process is down, or by other processes, are not delivered to it. Concurrent changes to one event can arrive out of order; see [order](../concepts/delivery.md#order). |
| Observability sink | Called inline for every change and failure; failures are logged. |

If the process stops after storing feedback but before its handlers finish, the
feedback is safe in the store but the handlers do not run again by
themselves. For work that must happen, make the handler enqueue durable work,
or reconcile from the store: for example, query `RECEIVED` feedback older than
a few minutes and process it again.

## Choosing a store

| Store | Use for |
|---|---|
| `InMemoryFeedbackStore` | Tests, development, short-lived processes. Contents are lost on exit. |
| [SQLite store](../how-to/custom-store.md#a-complete-sqlite-store) | One process or a few, on one machine, where feedback must survive restarts. |
| [PostgreSQL store](../examples/production.md#postgresql-store) | Production: many processes and machines, high write volume, SQL-side filtering. |

## Operating it

- Alert on `feedback.failed`, grouped by `stage`.
- Run [`expire_overdue`](../how-to/expire-feedback.md) on a schedule, so
  abandoned feedback is closed.
- Keep subscribers and handlers fast; move slow work to a queue.
- Close streams you no longer read: undelivered events are buffered without
  limit.
