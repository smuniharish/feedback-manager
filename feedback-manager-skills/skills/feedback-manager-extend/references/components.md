# Every other extension point

## Contents

- [Feedback pipeline](#feedback-pipeline)
- [Learning and retrieval storage](#learning-and-retrieval-storage)
- [agxai components](#agxai-components)
- [Certifying with the kits](#certifying-with-the-kits)

## Feedback pipeline

Pass each to `FeedbackManager(...)` under the argument shown.

| Argument | Subclass | Implement |
|---|---|---|
| `store` | `contracts.FeedbackStore` | `create`, `get`, `transition`, `query`; see [store.md](store.md) |
| `router` | `contracts.FeedbackRouter` | `async route(feedback) -> Sequence[FeedbackHandler]`; or use `routing.DefaultFeedbackRouter([RoutingRule(...)])` |
| (in a router) | `contracts.FeedbackHandler` | `async handle(feedback) -> FeedbackHandlerResult` |
| `correlator` | `contracts.FeedbackCorrelator` | `async correlate(feedback) -> str` |
| `lifecycle_policy` | `contracts.FeedbackLifecyclePolicy` | `authorize_transition(feedback, target)`; raise to refuse |
| `redaction_policy` | `contracts.FeedbackRedactionPolicy` | `redact(feedback) -> FeedbackEvent` |
| `provenance_provider` | `contracts.ProvenanceProvider` | `async resolve(feedback) -> FeedbackProvenanceReference \| None` |
| `observability_sink` | `observability.ObservabilitySink` | `emit(event)`; runs inline, so hand network calls to a worker |
| `manager.subscribe(...)` | `contracts.FeedbackSubscriber` | `async __call__(feedback)`; a plain async function also works, wrapped in `FunctionSubscriber` |
| `learning_scope_resolver` | `learning.ScopeResolver` | `__call__(feedback) -> ArtifactScope \| None`; a plain function also works |
| `learning_change_source` | `contracts.changes.FeedbackChangeSource` | `async read_changes(*, after, limit) -> FeedbackChangePage` |
| `learning_job_store` | `learning.LearningJobStore` | the job queue; see below |

## Learning and retrieval storage

| Component | Subclass | Abstract members |
|---|---|---|
| Artifact repository | `storage.artifacts.ArtifactRepository` | `create`, `get`, `get_many`, `versions`, `update(artifact, *, expected_version)`, `query`, `health`; class flags `durable` and `atomic_projection_intents` |
| Job queue | `learning.LearningJobStore` | `enqueue`, `claim`, `complete`, `retry`, `dead_letter`, `requeue`, `get`, `list`, `counts`, `checkpoint`, `save_checkpoint`; class flag `durable` |
| Vector database | `storage.vector.VectorStore` | `capabilities`, `upsert_many`, `get`, `search`, `delete`, `health` |
| Graph database | `storage.graph.GraphStore` | `capabilities`, `upsert_nodes`, `upsert_relationships`, `get_nodes`, `traverse`, `delete_nodes`, `delete_relationships`, `health` |
| Embedding model | `embeddings.EmbeddingProvider` | `space`, `embed_documents`, `embed_query`, `health` |
| Validation policy | `learning.validation.LearningValidationPolicy` | `evaluate(artifact, *, as_of=None) -> ValidationDecision` |
| Telemetry | `observability.operations.OperationObserver` | `observe(name)` returning a context manager |

Set `durable = True` only when data survives a process restart; the manager's
`require_durable_learning=True` trusts it. Every read and write takes a
`TenantScope`; a store must never return another tenant's or namespace's
records. `update` is optimistic: raise `feedback_manager.learning.ArtifactConflictError` when the current
version is not `expected_version`. To support `LearningEngine(..., atomic_publication=True)`, also
override `update_with_projection`, `pending_projections`, and
`acknowledge_projection`, and set
`atomic_projection_intents = True`.

The bundled LanceDB and LadybugDB stores accept your own client
(`LanceDBVectorStore(uri, connection=...)`,
`LadybugGraphStore(database=...)`); they do not close an injected client.

## agxai components

feedback-manager passes agxai components through without wrapping them:

```python
from feedback_manager import FeedbackManager
from feedback_manager.integrations.provenance import (
    AgxaiProvenanceProvider,
    create_agxai_runtime,
)

runtime = create_agxai_runtime(store=MyProvenanceStore())  # an agxai.ProvenanceStore
manager = FeedbackManager(provenance_provider=AgxaiProvenanceProvider(runtime))
```

`create_agxai_runtime` also accepts `config`, `registry`, `plugins`,
`custom_state_capture`, `observability`, `attribution`, `explanation`,
`policy`, and `capture_policy`. Each must subclass its agxai base class, or it
raises `FeedbackConfigurationError`. `AgxaiProvenanceProvider(store=...)`
builds a runtime around a store directly. Certify an agxai store with
`agxai.testing.ProvenanceStoreContract`, or use the agxai-extend skill.

## Certifying with the kits

```python
from feedback_manager.testing import VectorStoreContract


class TestMyVectorStore(VectorStoreContract):
    async def make_vector_store(self) -> MyVectorStore:
        return MyVectorStore(...)  # new and empty for every test
```

| Kit | Factory method |
|---|---|
| `FeedbackStoreContract` | `make_store` |
| `ArtifactRepositoryContract` | `make_repository` |
| `LearningJobStoreContract` | `make_job_store` |
| `VectorStoreContract` | `make_vector_store` |
| `GraphStoreContract` | `make_graph_store` |
| `EmbeddingProviderContract` | `make_provider` |

The kit tests are synchronous and run their own event loop, so they work
under plain pytest, unittest, or `scripts/check_component.py`.
