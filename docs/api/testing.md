# Testing

`feedback_manager.testing` holds conformance tests for your own components.
Subclass a contract in a test module and return a new, empty component from its
factory; your test runner then collects one test per promise the component
makes. The tests are synchronous, need neither pytest nor an async plugin, and
release the component with `aclose()` or `close()` after each test.

```python
from feedback_manager.testing import FeedbackStoreContract, VectorStoreContract


class TestPostgresFeedbackStore(FeedbackStoreContract):
    async def make_store(self):
        return await PostgresFeedbackStore.open(TEST_DSN, table=new_table_name())


class TestQdrantVectorStore(VectorStoreContract):
    async def make_vector_store(self):
        return QdrantVectorStore(url=TEST_URL, collection=new_collection_name())
```

| Contract | Factory | Certifies |
| --- | --- | --- |
| `FeedbackStoreContract` | `make_store` | `FeedbackStore` |
| `ArtifactRepositoryContract` | `make_repository` | `ArtifactRepository` |
| `VectorStoreContract` | `make_vector_store` | `VectorStore` |
| `GraphStoreContract` | `make_graph_store` | `GraphStore` |
| `LearningJobStoreContract` | `make_job_store` | `LearningJobStore` |
| `EmbeddingProviderContract` | `make_provider` | `EmbeddingProvider` |

Every bundled implementation passes these tests, including the LanceDB and
LadybugDB adapters. For an agxai `ProvenanceStore`, use
`agxai.testing.ProvenanceStoreContract`.

::: feedback_manager.testing.FeedbackStoreContract

::: feedback_manager.testing.ArtifactRepositoryContract

::: feedback_manager.testing.VectorStoreContract

::: feedback_manager.testing.GraphStoreContract

::: feedback_manager.testing.LearningJobStoreContract

::: feedback_manager.testing.EmbeddingProviderContract
