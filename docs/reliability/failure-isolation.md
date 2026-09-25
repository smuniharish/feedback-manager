# Failure isolation

Feedback should not destabilize the agent application it is observing.

Defaults:

- store failures are blocking
- routing, handler, subscriber, and provenance failures are best-effort

That means a broken handler or missing provenance lookup does not usually break `submit()`.

See:

- [Failure architecture](../architecture/FAILURE_MODEL.md)
- `FailurePolicy`

