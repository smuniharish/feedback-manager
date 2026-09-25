# API Contract

The root package exports a deliberately small, stable public surface.

## Public names in `__all__`

| Name | Description |
| --- | --- |
| `CorrelationContext` | Links feedback to execution and related feedback records |
| `ExecutionContext` | Framework-independent execution identifiers |
| `FeedbackCategory` | Open string type describing what kind of feedback an event represents |
| `FeedbackConfigurationError` | Raised for package or dependency misconfiguration |
| `FeedbackCorrelationError` | Raised when correlation or provenance resolution fails |
| `FeedbackEvent` | Central feedback record model |
| `FeedbackHandlerError` | Raised when a handler fails |
| `FeedbackLifecycleError` | Raised for illegal lifecycle transitions |
| `FeedbackManager` | Main application service for submission, retrieval, routing, and lifecycle |
| `FeedbackManagerError` | Base class for all package exceptions |
| `FeedbackNotFoundError` | Raised when a feedback id does not exist |
| `FeedbackProvenanceReference` | Framework-independent provenance pointer |
| `FeedbackQuery` | Query object used by stores and `FeedbackManager.query()` |
| `FeedbackRoutingError` | Raised when routing fails |
| `FeedbackSerializationError` | Raised when serialization fails |
| `FeedbackSource` | Open string type describing who or what produced feedback |
| `FeedbackStatus` | Closed lifecycle status enum |
| `FeedbackStoreError` | Raised when persistence operations fail |
| `FeedbackTarget` | Model describing what the feedback is about |
| `FeedbackTargetType` | Open string type describing the kind of target |
| `FeedbackValidationError` | Raised when a feedback model is invalid |
| `Subscription` | Cancellation handle returned by `subscribe()` |
| `__version__` | Package version string |
| `validate_transition` | Validates lifecycle transitions for custom store implementations |

## Specialized public namespaces

The root namespace stays small. Specialized capabilities are intentionally
grouped under stable public namespaces:

- framework helpers: `feedback_manager.integrations.langchain` and
  `feedback_manager.integrations.langgraph`
- extension contracts: `feedback_manager.contracts`
- default implementations for storage, routing, policies, handlers, and
  observability: their documented package namespaces

Undocumented modules and physical source-file paths are implementation
details and are not part of the compatibility contract.
