# Errors

Every error feedback-manager raises derives from `FeedbackManagerError`, so one
`except FeedbackManagerError` clause covers the whole package, while each
subclass identifies one failure mode. When an error wraps another exception,
the original is chained as `__cause__`.

```text
FeedbackManagerError
├── FeedbackValidationError      (also a ValueError)
├── FeedbackConfigurationError
├── FeedbackNotFoundError        (also a LookupError)
├── FeedbackLifecycleError
│   └── FeedbackConflictError
├── FeedbackStoreError
├── FeedbackCorrelationError
├── FeedbackRoutingError
├── FeedbackHandlerError
└── FeedbackSubscriberError
```

::: feedback_manager.errors.FeedbackManagerError

::: feedback_manager.errors.FeedbackValidationError

::: feedback_manager.errors.FeedbackConfigurationError

::: feedback_manager.errors.FeedbackNotFoundError

::: feedback_manager.errors.FeedbackLifecycleError

::: feedback_manager.errors.FeedbackConflictError

::: feedback_manager.errors.FeedbackStoreError

::: feedback_manager.errors.FeedbackCorrelationError

::: feedback_manager.errors.FeedbackRoutingError

::: feedback_manager.errors.FeedbackHandlerError

::: feedback_manager.errors.FeedbackSubscriberError
