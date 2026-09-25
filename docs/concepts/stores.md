# Stores

Stores persist feedback events.

Contract methods:

- `create()`
- `get()`
- `update()`
- `transition()`
- `query()`
- `list()`

Bundled implementation:

- `InMemoryFeedbackStore`

The in-memory store is concurrency-safe and idempotency-aware, but it is a reference implementation, not a production database adapter.

Production applications should usually implement `FeedbackStore` for their own database or event store.

See:

- [Advanced custom store guide](../advanced/custom-store.md)
- [Extensibility architecture](../architecture/EXTENSIBILITY_MODEL.md)

