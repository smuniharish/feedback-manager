# API Contract

The root package exports a deliberately small public surface from `src/feedback_manager/__init__.py`.

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

## Intentionally not exported at the root

The root package does not export everything in the repository. For example:

- concrete storage/router/handler implementations live in subpackages
- LangChain/LangGraph/XAI integrations live under `feedback_manager.integrations`
- contracts live under `feedback_manager.contracts`

This keeps the root import surface stable.

