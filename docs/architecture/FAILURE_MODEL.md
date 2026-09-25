# Failure Model

Failure isolation is implemented in `src/feedback_manager/policies/failure.py`.

## Failure modes

Two modes exist:

### `FailureMode.BEST_EFFORT`

- catches the exception
- optionally invokes `on_error`
- logs a warning
- returns `None`

Used when feedback plumbing must not crash the application flow.

### `FailureMode.BLOCKING`

- re-raises the exception to the caller

Used when the failure means the feedback operation itself did not succeed in a meaningful way.

## Stages

Failures are isolated per `FeedbackStage`:

- `STORE`
- `ROUTING`
- `HANDLER`
- `SUBSCRIBER`
- `SERIALIZATION`
- `PROVENANCE`

## Default stage policy

`FailurePolicy()` defaults to:

| Stage | Default mode |
| --- | --- |
| `STORE` | `BLOCKING` |
| `ROUTING` | `BEST_EFFORT` |
| `HANDLER` | `BEST_EFFORT` |
| `SUBSCRIBER` | `BEST_EFFORT` |
| `SERIALIZATION` | `BLOCKING` |
| `PROVENANCE` | `BEST_EFFORT` |

## Why these defaults exist

- if persistence fails, submission should usually fail visibly
- if provenance lookup fails, feedback should still be recorded
- if one handler fails, other handlers and the caller should not be taken down
- if one subscriber fails, other subscribers should still receive notifications

## Where isolation is applied

In `FeedbackManager`:

- provenance resolution is wrapped as `PROVENANCE`
- store operations are wrapped as `STORE`
- router selection is wrapped as `ROUTING`
- each handler invocation is wrapped as `HANDLER`
- each subscriber invocation is wrapped as `SUBSCRIBER`

## What isolation means in practice

Examples:

- a broken audit handler does not stop `submit()`
- a failing subscriber does not stop lifecycle transitions
- missing provenance does not stop feedback persistence
- a store failure still fails the operation by default

## What is not built in

The package does not currently implement:

- retries
- backoff
- dead-letter queues
- circuit breakers
- handler concurrency pools

Those belong in custom stores, routers, handlers, or application orchestration.

