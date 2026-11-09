# Retrieval, vector and graph stores, embeddings

## Contents

- [Strategies](#strategies)
- [Requests and results](#requests-and-results)
- [Hybrid retrieval with LanceDB and LadybugDB](#hybrid-retrieval-with-lancedb-and-ladybugdb)
- [Embedding providers](#embedding-providers)
- [Changing the embedding space](#changing-the-embedding-space)
- [Bring your own database client](#bring-your-own-database-client)

## Strategies

| Retriever | Needs | Use when |
|---|---|---|
| `StructuredRetriever` | the artifact repository | default; no model or database service |
| `VectorRetriever` | repository, `VectorStore`, `EmbeddingProvider` | lessons are phrased differently from tasks |
| `GraphRetriever` | repository, `GraphStore` | related lessons should come along |
| `HybridRetriever` | repository, vectors, embeddings, optional graph | production retrieval |

`LearningEngine.retrieve` uses structured retrieval unless you pass
`LearningEngine(repository, retriever=...)`. Any retriever can go straight to
a framework context injector. A missing backend is never silently replaced:
asking for graph expansion without a graph store raises
`UnsupportedCapabilityError`.

## Requests and results

```python
from feedback_manager.retrieval import RetrievalRequest

request = RetrievalRequest(
    scope=scope,  # authenticated tenant and namespace
    agent_id="researcher",  # whose lessons apply
    task="validate search results",
    limit=5,
    min_confidence=0.7,
    max_context_chars=4000,
    graph_depth=1,  # hybrid and graph retrievers only
)
result = await retriever.retrieve(request)
for item in result.results:
    print(item.artifact.title, item.relevance_score, item.explanation)
text = result.as_context()  # titles and content only
```

Other filters: `agent_type`, `task_type`, `tool`, `workflow`,
`artifact_types`, `max_candidates`. Retrieval always rechecks the current
artifact version, its status, the validation policy, and agent applicability,
so stale projections are ignored rather than trusted.

Hybrid scores weight structured relevance 30%, vector relevance 35%, graph
connection 10%, evidence confidence 15%, and independent support 10%. They
rank; they are not probabilities.

## Hybrid retrieval with LanceDB and LadybugDB

```bash
pip install "feedback-manager[lancedb,ladybugdb]"
```

```python
from feedback_manager.embeddings import HashingEmbeddingProvider
from feedback_manager.retrieval import ArtifactIndexer, HybridRetriever
from feedback_manager.storage.graph.ladybug import LadybugGraphStore
from feedback_manager.storage.vector.lancedb import LanceDBVectorStore

vectors = await LanceDBVectorStore.open("./vectors")
async with LadybugGraphStore("./graph.db") as graph:
    provider = HashingEmbeddingProvider()
    indexer = ArtifactIndexer(repository, vectors, provider, graph=graph)
    await indexer.drain_intents(scope)  # at startup and periodically
    await indexer.reindex(scope)  # repair or first build
    retriever = HybridRetriever(repository, vectors, provider, graph=graph)
    result = await retriever.retrieve(request)
```

- The indexer projects only active artifact versions and removes others.
- LanceDB keeps each embedding space in its own table and filters tenant and
  namespace before ranking. Over-fetch stops at `max_search_candidates`
  (10,000 by default) and raises rather than returning partial results.
- LadybugDB runs parameterized Cypher with scoped endpoints. One writer
  process per database; close it when done, preferably with `async with`.
- There is no transaction across the repository, LanceDB, and LadybugDB. With
  `LearningEngine(repository, atomic_publication=True)` and a repository that
  supports projection intents, publication records an intent that
  `drain_intents` applies until it succeeds.

## Embedding providers

`HashingEmbeddingProvider` is deterministic, offline feature hashing: a
lexical baseline for tests and demos, not a semantic model. For a real model,
wrap its SDK:

```python
from feedback_manager.embeddings import CallableEmbeddingProvider, EmbeddingSpace

space = EmbeddingSpace(
    provider="my-provider",
    model="my-model",
    revision="pinned-revision",
    dimensions=768,
    normalized=True,
    document_instruction="document: ",
    query_instruction="query: ",
)
provider = CallableEmbeddingProvider(space, embed_documents, embed_queries=embed_query_batch)
```

Both callables take a sequence of strings and return a sequence of vectors,
sync or async. The wrapper checks counts, dimensions, and finite values. The
host owns credentials, timeouts, and retries. To write a provider class,
subclass `EmbeddingProvider` and certify it with
`feedback_manager.testing.EmbeddingProviderContract`.

## Changing the embedding space

Model, revision, dimensions, normalization, and both instructions define one
space. To change any of them:

1. Create a provider with the new space.
2. Run a new `ArtifactIndexer` into it and check real queries.
3. Switch the retriever to the new provider.
4. Keep the old space until nothing uses it, for rollback.

Never compare vectors from different spaces, even with equal dimensions.

## Bring your own database client

Pass a connection you configured, for example with credentials or cloud
storage options:

```python
import lancedb
import ladybug

vectors = LanceDBVectorStore(uri, connection=await lancedb.connect_async(uri))
graph = LadybugGraphStore(database=ladybug.Database(path))
```

The store does not close an injected database; your code owns it. For a
different vector or graph database, use the feedback-manager-extend skill.
