# Concurrency

Concurrency safety is currently centered on the store and the lifecycle model.

- `InMemoryFeedbackStore` uses an `asyncio.Lock`
- submission dedup is protected by the store
- same-state lifecycle transitions are idempotent
- multiple manager instances remain independent

These guarantees are covered by the concurrency test suite.

See the full [concurrency architecture document](../architecture/CONCURRENCY_MODEL.md).
