# Idempotency

Two forms of idempotency exist in the implementation.

## Submission idempotency

`FeedbackStore.create()` must deduplicate by `idempotency_key`.

If `submit()` hits an existing event with the same key, the manager returns the existing event without trying to recreate or re-transition it.

## Lifecycle idempotency

Same-state transitions are legal:

- `ACKNOWLEDGED -> ACKNOWLEDGED`
- `HANDLED -> HANDLED`
- and so on

This is what makes concurrent retry scenarios safe.

