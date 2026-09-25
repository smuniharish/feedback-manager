# Lifecycle

Feedback records move through a closed lifecycle.

Happy path:

```text
RECEIVED -> ACKNOWLEDGED -> HANDLED -> RESOLVED
```

Terminal alternatives:

- `REJECTED`
- `CANCELLED`
- `EXPIRED`

Important rule:

- `resolve()` requires the event to already be `HANDLED`
- `ACKNOWLEDGED -> RESOLVED` is illegal

Helper methods on `FeedbackManager`:

- `acknowledge(feedback_id)`
- `mark_handled(feedback_id)`
- `resolve(feedback_id, resolution=...)`
- `reject(feedback_id, reason=...)`
- `cancel(feedback_id, reason=...)`
- `expire(feedback_id)`

For the exact transition table and idempotency semantics, see [Lifecycle architecture](../architecture/LIFECYCLE.md).

