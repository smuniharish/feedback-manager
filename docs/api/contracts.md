# Contracts

Every extension point is an abstract base class. Subclass one, implement its
abstract methods, and pass an instance to `FeedbackManager`, `LearningEngine`,
`BackgroundLearner`, or a retriever. `FeedbackManager` checks each collaborator
with `isinstance` and raises `FeedbackConfigurationError` for anything else, so
a mistake fails at start-up instead of on the first event.

Two contracts also accept plain functions: `FeedbackManager.subscribe` wraps an
`async def` in `FunctionSubscriber`, and `learning_scope_resolver` wraps a
function in `FunctionScopeResolver`.

| Extension point | Replaces | Contract tests |
| --- | --- | --- |
| `FeedbackStore` | Feedback persistence | `FeedbackStoreContract` |
| `FeedbackChangeSource` | The durable change feed for background learning | |
| `FeedbackRouter`, `FeedbackHandler` | Routing and handling | |
| `FeedbackCorrelator` | Correlation IDs | |
| `FeedbackLifecyclePolicy`, `FeedbackRedactionPolicy` | Lifecycle and redaction rules | |
| `FeedbackSubscriber` | Change notifications | |
| `ProvenanceProvider` | Provenance resolution, such as agxai | |
| `ObservabilitySink`, `OperationObserver` | Events, metrics, and traces | |
| `ArtifactRepository` | Learned artifact persistence | `ArtifactRepositoryContract` |
| `VectorStore` | Vector projections | `VectorStoreContract` |
| `GraphStore` | Graph projections | `GraphStoreContract` |
| `LearningJobStore` | Background learning jobs | `LearningJobStoreContract` |
| `EmbeddingProvider` | Embeddings | `EmbeddingProviderContract` |
| `ScopeResolver` | Artifact scope for background learning | |
| `ArtifactLearner`, `LearningValidationPolicy` | Learning and validation | |
| `LearnedArtifactRetriever` | Retrieval | |
| `FrameworkAdapter` | Agent framework integration | |

Run the contract tests from [`feedback_manager.testing`](testing.md) against
your own implementation, and see
[customize every component](../how-to/customize-components.md) for how each
one plugs in.

## Storage

::: feedback_manager.contracts.FeedbackStore

::: feedback_manager.contracts.FeedbackQuery

::: feedback_manager.storage.InMemoryFeedbackStore

::: feedback_manager.contracts.changes.FeedbackChangeSource

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

::: feedback_manager.contracts.FunctionSubscriber

## Provenance

::: feedback_manager.contracts.ProvenanceProvider

## Framework adapters

::: feedback_manager.integrations.base.FrameworkAdapter
