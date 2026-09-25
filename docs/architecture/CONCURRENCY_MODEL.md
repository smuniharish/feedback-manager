# Concurrency Model

The package is async-first and uses standard `asyncio` primitives.

## Primitives used in the implementation

### `asyncio.Lock`

`InMemoryFeedbackStore` uses a single `asyncio.Lock` to protect:

- the ordered event map
- the idempotency-key index

This guarantees that concurrent `create()`, `update()`, `get()`, and `transition()` calls do not corrupt internal state.

### `asyncio.Queue`

`FeedbackManager.stream()` creates per-stream `asyncio.Queue[FeedbackEvent]` instances and appends them to the manager's internal `_stream_queues` list. `_notify_subscribers()` pushes events into these queues with `put_nowait()`.

### `asyncio.TaskGroup`

The implementation does not depend on `TaskGroup`, but the concurrency tests use it to exercise real concurrent use:

- concurrent submissions
- duplicate idempotent submissions
- concurrent lifecycle transitions
- concurrent subscriber notification scenarios

## Tested concurrency behavior

`tests/concurrency/test_concurrency.py` verifies:

1. **concurrent submissions all persist**  
   50 simultaneous `submit()` calls produce 50 stored events.

2. **concurrent duplicate idempotent submissions deduplicate**  
   20 simultaneous submissions with the same `idempotency_key` produce one stored event and one shared `feedback_id`.

3. **concurrent lifecycle retries are idempotent**  
   10 simultaneous `acknowledge()` calls against the same event all succeed and the final state is `ACKNOWLEDGED`.

4. **multiple subscribers all receive events**  
   multiple subscribers observe all submitted events.

5. **stream cancellation is isolated**  
   cancelling one active stream consumer does not break future manager usage.

## Multi-instance independence

There is no module-level singleton manager, router, or store. Each `FeedbackManager` instance owns its own dependencies and internal subscriber/stream state. `tests/unit/test_manager.py` explicitly verifies that two managers remain isolated.

## Current execution model

Important limits:

- handlers are invoked sequentially, not in parallel
- subscribers are invoked sequentially, not via background task fan-out
- routing is pure selection, not concurrent execution

This favors deterministic, easy-to-reason-about behavior.

