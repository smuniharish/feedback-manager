# Security Model

`feedback-manager` provides a few security-relevant extension points, but it does **not** ship a full security framework.

## What exists in the codebase

### Redaction and filtering hook

`FeedbackManager` accepts `redaction_policy: FeedbackPolicy | None`.

`FeedbackPolicy` is an abstract base class with:

```python
def apply(self, feedback: FeedbackEvent) -> FeedbackEvent: ...
```

If provided, it runs before correlation/provenance/persistence during `submit()`. This is the main hook for:

- payload redaction
- metadata filtering
- dropping sensitive fields
- normalizing user-supplied content before storage

### Persistence boundary

`FeedbackStore` is abstract. Durable storage, encryption, row-level authorization, auditing, and retention enforcement are the responsibility of the concrete store implementation chosen by the application.

### Retention predicate

`RetentionPolicy` can decide whether a non-terminal event should be considered expired. It does not delete data or run a scheduler; applications must enforce retention operationally.

### Observability sink boundary

`ObservabilitySink` receives lightweight events. If observability data is sensitive, the application must provide a sink implementation that redacts or filters attributes before forwarding them.

## What does not exist

The package does not currently implement:

- authentication
- authorization
- encryption at rest
- transport security
- secret management
- tenant isolation logic
- automatic PII detection
- deletion workflows

## Practical security pattern

In production, combine:

1. a `FeedbackPolicy` for redaction
2. a custom `FeedbackStore` with the application's access controls
3. a retention process driven by `RetentionPolicy`
4. a custom `ObservabilitySink` that avoids leaking sensitive payloads

## Key takeaway

Security-sensitive behavior belongs at the application and infrastructure boundary, not inside this library's core domain model.

