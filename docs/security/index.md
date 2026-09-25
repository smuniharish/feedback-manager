# Security guidance

`feedback-manager` does not provide a built-in security platform, but it does provide useful extension points.

Recommended production pattern:

1. apply a `FeedbackPolicy` to redact or normalize sensitive payloads before storage
2. use a custom `FeedbackStore` that enforces your application's access controls
3. use `RetentionPolicy` plus application scheduling for expiration and cleanup
4. use a custom `ObservabilitySink` if observability data must be filtered

Deep dive: [Security architecture](../architecture/SECURITY_MODEL.md).

