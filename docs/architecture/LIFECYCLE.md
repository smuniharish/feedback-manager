# Lifecycle Model

The lifecycle state machine is implemented in `src/feedback_manager/core/lifecycle.py`.

## States

```text
CREATED
  -> RECEIVED
     -> ACKNOWLEDGED
        -> HANDLED
           -> RESOLVED
```

Additional terminal outcomes:

- `REJECTED`
- `CANCELLED`
- `EXPIRED`

## Legal transitions

The transition table is explicit in `LEGAL_TRANSITIONS`.

### From `CREATED`

- `RECEIVED`
- `CANCELLED`
- `EXPIRED`

### From `RECEIVED`

- `ACKNOWLEDGED`
- `REJECTED`
- `CANCELLED`
- `EXPIRED`

### From `ACKNOWLEDGED`

- `HANDLED`
- `REJECTED`
- `CANCELLED`
- `EXPIRED`

### From `HANDLED`

- `RESOLVED`
- `REJECTED`
- `CANCELLED`

### From terminal states

`RESOLVED`, `REJECTED`, `CANCELLED`, and `EXPIRED` have no outgoing transitions except to themselves.

## Happy path

The intended happy path is:

```text
RECEIVED -> ACKNOWLEDGED -> HANDLED -> RESOLVED
```

This detail matters:

- `resolve()` requires the event to already be `HANDLED`
- you cannot jump directly from `ACKNOWLEDGED` to `RESOLVED`

That constraint is enforced by `validate_transition()`.

## Idempotency behavior

Same-state transitions are always legal:

- `RECEIVED -> RECEIVED`
- `ACKNOWLEDGED -> ACKNOWLEDGED`
- and so on

`validate_transition()` marks these as `idempotent=True` in the returned `LifecycleTransition` record.

This is what allows concurrent retries such as multiple `acknowledge()` calls to succeed safely.

## Runtime behavior in `FeedbackManager`

`FeedbackManager.submit()`:

1. creates a `FeedbackEvent` with status `CREATED`
2. persists it
3. immediately transitions it to `RECEIVED`

Lifecycle helper methods then advance the record:

- `acknowledge()`
- `mark_handled()`
- `resolve()`
- `reject()`
- `cancel()`
- `expire()`

## Resolution metadata

`FeedbackManager._transition()` optionally merges resolution metadata into `event.metadata["resolution"]` before the status transition. This is how `resolve()`, `reject()`, and `cancel()` attach outcome details.

## Failure mode

Illegal transitions raise `FeedbackLifecycleError`, including:

- current status
- requested status
- feedback id

Examples of illegal moves:

- `CREATED -> RESOLVED`
- `ACKNOWLEDGED -> RESOLVED`
- `HANDLED -> RECEIVED`
- `RESOLVED -> ACKNOWLEDGED`

