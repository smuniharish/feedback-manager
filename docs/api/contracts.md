# Contracts

Implement a contract to replace one of `FeedbackManager`'s collaborators.
Stateful extension points are abstract base classes; single-method callables
are protocols, which plain functions and objects satisfy without subclassing.

## Storage

::: feedback_manager.contracts.FeedbackStore

::: feedback_manager.contracts.FeedbackQuery

::: feedback_manager.storage.InMemoryFeedbackStore

## Routing

::: feedback_manager.contracts.FeedbackRouter

::: feedback_manager.contracts.FeedbackHandler

::: feedback_manager.contracts.FeedbackHandlerResult

## Correlation

::: feedback_manager.contracts.FeedbackCorrelator

::: feedback_manager.correlation.DefaultFeedbackCorrelator

::: feedback_manager.correlation.default_correlation_id

## Policies

::: feedback_manager.contracts.FeedbackLifecyclePolicy

::: feedback_manager.contracts.FeedbackRedactionPolicy

## Subscribers

::: feedback_manager.contracts.FeedbackSubscriber
