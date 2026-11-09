# Storage, embeddings and hybrid retrieval

Install the real optional database adapters:

```bash
pip install "feedback-manager[lancedb,ladybugdb]"
```

```python
from feedback_manager.embeddings import HashingEmbeddingProvider
from feedback_manager.retrieval import ArtifactIndexer, HybridRetriever, RetrievalRequest
from feedback_manager.storage.graph.ladybug import LadybugGraphStore
from feedback_manager.storage.vector.lancedb import LanceDBVectorStore

vectors = await LanceDBVectorStore.open("./vectors")
async with LadybugGraphStore("./graph.db") as graph:
    provider = HashingEmbeddingProvider()
    await ArtifactIndexer(repository, vectors, provider, graph=graph).reindex(scope)
    retriever = HybridRetriever(repository, vectors, provider, graph=graph)
    result = await retriever.retrieve(
        RetrievalRequest(scope=scope, agent_id="researcher", task="validate results", graph_depth=2)
    )
```

`repository` is the authoritative `ArtifactRepository`; `scope` is an authenticated
`TenantScope`. The [complete example](background-learning.md) constructs both.

## Choose a strategy

| Strategy | Dependencies and behavior |
|---|---|
| `StructuredRetriever` | Repository only; scoped filters and token overlap |
| `VectorRetriever` | Repository, vector store, embedding provider; vector candidates |
| `GraphRetriever` | Repository and graph store; active scoped seeds, one hop by default |
| `HybridRetriever` | Repository, vectors and embeddings; optionally graph expansion |

The default `LearningEngine` uses structured retrieval, requiring no model or
database service. Inject a configured retriever with `LearningEngine(...,
retriever=retriever)` or give it directly to a framework context injector.
No missing backend is silently substituted. A requested graph traversal without
a graph store raises `UnsupportedCapabilityError`.

Hybrid scoring combines structured relevance (30%), vector relevance (35%),
graph connection (10%), evidence confidence (15%) and independent support (10%).
Results expose component explanations and deterministic tie-breaking. Scores are
ranking heuristics, not calibrated probabilities or evidence of model training.

Requests bound candidates, results, graph depth and rendered characters. Graph
expansion additionally fails explicitly beyond 10,000 nodes or 20,000
relationships. Graph pagination is deterministic for a stable graph, not a
snapshot transaction across mutations.
Retrieval budgets cover title/content bodies; framework context injectors also
budget the final escaped text and delimiters, dropping whole lower-ranked
artifacts rather than truncating guidance mid-sentence. They return no context
when the wrapper and first artifact cannot fit.

## Authorities and lifecycle

The indexer projects active artifact versions. Non-active artifacts have their
current-space vectors and artifact graph node removed. Graph edges represent
applicability and valid evidence. Artifact retrieval always checks current
versions, review eligibility, confidence and agent applicability. Wrong-tenant
responses from an adapter are errors; stale or missing projections are ignored.

The vector adapter isolates embedding spaces in separate tables and applies
tenant/namespace predicates before ranking. LanceDB metadata filters use exact
top-level matches; visibility and agent metadata have native prefilters, while
arbitrary metadata filtering may over-fetch candidates. Over-fetch and tie
resolution stop at the adapter's `max_search_candidates` safety bound (10,000 by
default) and raise explicitly if they cannot complete within it.
Vector indexes are optional, physical per-space indexes and approximate once
created. Small datasets may not satisfy LanceDB's IVF-PQ training requirements.
Zero vectors have no cosine match in LanceDB.

LadybugDB executes parameterized Cypher, validates scoped endpoints and writes
batches transactionally. An embedded database supports **one writer process** or
multiple read-only processes, not concurrent writer replicas on shared storage.
Close it after operations finish, preferably with its async context manager.

## Embedding providers and reindexing

`HashingEmbeddingProvider` is deterministic, offline signed feature hashing of
words and bigrams. It is a lexical baseline, **not** a neural semantic model.
For production model SDKs, use `CallableEmbeddingProvider` with sync or async
document/query callables:

```python
from feedback_manager.embeddings import CallableEmbeddingProvider, EmbeddingSpace

space = EmbeddingSpace(
    provider="my-provider", model="my-model", revision="pinned-revision",
    dimensions=768, normalized=True,
    document_instruction="document: ", query_instruction="query: ",
)
provider = CallableEmbeddingProvider(space, embed_documents, embed_queries=embed_query_batch)
```

Both callables accept a sequence of strings and return a sequence of vectors.
The wrapper checks output counts, dimensions and finite values and normalizes
when configured. The host owns authentication, timeouts, model downloads and SDK
retries. No network provider is loaded implicitly.

Model identity, revision, dimensions, normalization and both instructions define
one embedding space. To change any of them:

1. Create a provider with the new complete space identity.
2. Run a new indexer into that space; validate results with real queries.
3. Switch the retriever to the new provider.
4. Keep the old space and provider to roll back. Delete old data only once
   nothing references it.

Never compare vectors from different spaces, even when dimensions match.
