# Bring your own components

Every collaborator feedback-manager uses is an abstract base class with a
bundled implementation. Pass your own and nothing else changes: the manager,
the learning engine, the background learner, and the retrievers only call the
abstract methods. Downstream packages get the same choice, so agxai's
provenance store, LanceDB's connection, and LadybugDB's database can be yours
too.

## What you can replace

| Component | Base class | Plug it in with |
| --- | --- | --- |
| Feedback storage | `FeedbackStore` | `FeedbackManager(store=...)` |
| Change feed for learning | `FeedbackChangeSource` | `FeedbackManager(learning_change_source=...)` |
| Routing | `FeedbackRouter`, `FeedbackHandler` | `FeedbackManager(router=DefaultFeedbackRouter([RoutingRule(...)]))` |
| Correlation | `FeedbackCorrelator` | `FeedbackManager(correlator=...)` |
| Lifecycle and redaction | `FeedbackLifecyclePolicy`, `FeedbackRedactionPolicy` | `FeedbackManager(lifecycle_policy=..., redaction_policy=...)` |
| Subscribers | `FeedbackSubscriber`, or any async function | `manager.subscribe(...)` |
| Provenance | `ProvenanceProvider` | `FeedbackManager(provenance_provider=...)` |
| Observability | `ObservabilitySink`, `OperationObserver` | `FeedbackManager(observability_sink=...)`, `observer=` on learning components |
| Learned artifacts | `ArtifactRepository` | `LearningEngine(repository)` |
| Learning rules | `ArtifactLearner`, `LearningValidationPolicy` | `BackgroundLearner(extractor=...)`, `LearningEngine(validation_policy=...)` |
| Learning scope | `ScopeResolver`, or a function | `FeedbackManager(learning_scope_resolver=...)` |
| Durable jobs | `LearningJobStore` | `FeedbackManager(learning_job_store=...)` |
| Embeddings | `EmbeddingProvider`, or `CallableEmbeddingProvider` | `ArtifactIndexer(...)`, `HybridRetriever(...)` |
| Vectors and graph | `VectorStore`, `GraphStore` | `ArtifactIndexer(...)`, `HybridRetriever(...)` |
| Retrieval | `LearnedArtifactRetriever` | `LearningEngine(retriever=...)` |
| Agent frameworks | `FrameworkAdapter` | See [custom agents](custom-agents.md) |

Each base class is listed with its methods in [Contracts](../api/contracts.md).
The manager checks every collaborator with `isinstance` when it is built, so
a missing method fails at construction, not in the middle of a run.

## agxai provenance

`create_agxai_runtime` builds the agxai runtime feedback-manager links feedback
to. Each agxai component is optional and replaces agxai's default when given:

```python
from agxai import InMemoryProvenanceStore

from feedback_manager import FeedbackManager
from feedback_manager.integrations.provenance import (
    AgxaiProvenanceProvider,
    create_agxai_runtime,
)

store = InMemoryProvenanceStore()  # or any agxai ProvenanceStore subclass
runtime = create_agxai_runtime(application_id="support", store=store)
manager = FeedbackManager(provenance_provider=AgxaiProvenanceProvider(runtime))
```

`create_agxai_runtime` also accepts agxai's `config`, `registry`, `plugins`,
`custom_state_capture`, `observability`, `attribution`, `explanation`,
`policy`, and `capture_policy`, plus the `tenant_id`, `application_id`, and
`graph_id` identity fields. To reuse a runtime your agents already run, pass it
directly: `AgxaiProvenanceProvider(runtime)`. Without a runtime,
`AgxaiProvenanceProvider(store=...)` builds one around your store. agxai
rejects a component that does not subclass its base class; feedback-manager
raises that as `FeedbackConfigurationError`.

Certify an agxai store with agxai's own kit,
`agxai.testing.ProvenanceStoreContract`.

## LanceDB

`LanceDBVectorStore` opens its own connection by default. Pass connection
options, or a connection you already manage:

```python
import lancedb

from feedback_manager.storage.vector.lancedb import LanceDBVectorStore

# LanceDB's own connect options, such as cloud credentials:
vectors = await LanceDBVectorStore.open(
    "s3://bucket/vectors", connect_options={"storage_options": {"region": "eu-west-1"}}
)

# Or a connection shared with the rest of your application:
connection = await lancedb.connect_async("data/vectors")
vectors = LanceDBVectorStore("data/vectors", connection=connection)
```

`max_search_candidates` caps how many rows one search scans before filtering by
scope.

## LadybugDB

`LadybugGraphStore` opens a database at `path`, or uses a `ladybug.Database`
you pass. A database you pass stays yours: closing the store leaves it open.

```python
import ladybug

from feedback_manager.storage.graph.ladybug import LadybugGraphStore

graph = LadybugGraphStore("data/graph.lbug", database_options={"buffer_pool_size": 1 << 28})

database = ladybug.Database("data/graph.lbug")
graph = LadybugGraphStore(database=database)
graph.close()  # database is still open
```

`read_only=True` opens the database read-only, for retrieval-only processes.

## Certify your component

`feedback_manager.testing` holds a contract kit per storage and embedding base
class. Subclass the kit, implement its factory, and pytest runs every check the
bundled implementations pass:

```python
from feedback_manager.testing import FeedbackStoreContract

from myapp.feedback import PostgresFeedbackStore


class TestPostgresFeedbackStore(FeedbackStoreContract):
    async def make_store(self) -> PostgresFeedbackStore:
        return PostgresFeedbackStore(dsn="postgresql://localhost/test")
```

See [Testing kits](../api/testing.md) for every kit and its factory.

## A complete example

`examples/16_custom_components.py` wires a custom feedback store, a function
subscriber, a scope function, an agxai store, a LanceDB connection, and a
LadybugDB database together, then certifies the store with its kit:

```bash
uv run python examples/16_custom_components.py
```

```text
agxai store is the caller's: True
Feedback writes: 1; subscriber saw: ['received', 'acknowledged']
LanceDB vector store is healthy on the caller's connection
Injected LadybugDB database still open after store.close(): True
Certified CountingFeedbackStore: 6 contract tests passed
```

??? example "examples/16_custom_components.py"

    ```python
    --8<-- "examples/16_custom_components.py"
    ```
