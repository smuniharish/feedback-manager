# Security and data handling

feedback-manager is a library inside your process. It has no network
listeners, no users, and no credentials of its own. It stores and forwards
whatever feedback your application gives it, so feedback deserves the same
care as any other user data.

## What feedback contains

Feedback often carries personal or sensitive data: a user's comment, a
corrected answer, the prompt shown to a reviewer, an error message. Everything
you pass in `payload`, `metadata`, `target.metadata`, `execution_context`, and
`resolution` is stored, published to subscribers and streams, and handed to
handlers.

## Redact before anything else sees it

A `FeedbackRedactionPolicy` runs first in `submit`, before correlation,
provenance, storage, publication, and routing, so nothing downstream ever sees
the unredacted event:

```python
from feedback_manager import FeedbackEvent, FeedbackManager
from feedback_manager.contracts import FeedbackRedactionPolicy


class DropContactDetails(FeedbackRedactionPolicy):
    def redact(self, feedback: FeedbackEvent) -> FeedbackEvent:
        payload = {
            key: value
            for key, value in feedback.payload.items()
            if key not in {"email", "phone"}
        }
        return feedback.model_copy(update={"payload": payload})


manager = FeedbackManager(redaction_policy=DropContactDetails())
```

If the policy fails, `submit` raises `FeedbackValidationError` and nothing is
stored: unredacted data is never an acceptable fallback.

Identifiers, such as target IDs, idempotency keys, and execution context IDs,
are not redacted and appear in logs and observability events. Keep personal
data out of them.

## What the package logs

feedback-manager never includes payloads, metadata, or resolutions in its log
records or observability events. They contain IDs, the source, category,
target type, and status, counts, and, for failures, the exception type and
message. Exception messages come from the code that raised them, including your
handlers and stores, so keep personal data out of exception messages too.

`AuditFeedbackHandler` follows the same rule and is safe to use with ordinary
log retention.

## Data formats

Events are JSON: payloads, metadata, and resolutions must be JSON objects, and
stores serialize events to JSON. Nothing is pickled, and loading an event only
validates data; it never executes code. Values without a JSON form, such as an
arbitrary object passed as a human-in-the-loop prompt or response, are stored
as their string representation, so pass plain data.

## Authorization

feedback-manager does not know who calls it. Authenticate and authorize
callers in your application before calling the manager, and expose lifecycle
actions only to the people allowed to take them. A `FeedbackLifecyclePolicy`
can enforce rules that depend on the feedback itself; rules about the caller
can read the caller from your request context, for example a
`contextvars.ContextVar`.

## Retention

Feedback is kept until your store deletes it. Use a
[`RetentionPolicy`](../how-to/expire-feedback.md) to close stale feedback, and
your database's tooling to delete or archive closed feedback according to your
data retention requirements.

## The example infrastructure

The example stores build SQL with parameters and quote table names as
identifiers, never by formatting values into statements. The credentials in
`examples/compose.yaml` are local development defaults: never reuse them.

## Supply chain

Dependencies are declared with bounded version ranges, and releases are built
and published by GitHub Actions using PyPI Trusted Publishing, without
long-lived API tokens.

## Reporting a vulnerability

Please report vulnerabilities privately, as described in the
[security policy](https://github.com/smuniharish/feedback-manager/blob/master/SECURITY.md),
and not in public issues.
