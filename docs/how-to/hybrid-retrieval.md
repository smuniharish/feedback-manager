# Retrieval with RetrievalLoom

Feedback Manager uses RetrievalLoom for learned-artifact discovery. RetrievalLoom
is installed with the core package; its default offline indexes need no model
download or external database.

The default `LearningEngine` creates a manager-owned RetrievalLoom engine lazily.
Applications that need native component configuration can create and inject an
engine:

```python
from retrievalloom import EngineResources, LifecycleConfig, create_retrieval_engine

from feedback_manager.learning.engine import LearningEngine
from feedback_manager.retrieval import RetrievalLoomRetriever

retrieval_engine = create_retrieval_engine(
    lifecycle=LifecycleConfig(background=False),
    resources=EngineResources(data_dir="./retrieval-data"),
)
retriever = RetrievalLoomRetriever(repository, engine=retrieval_engine)
learning = LearningEngine(repository, retriever=retriever)
```

The injected engine and its components remain application-owned. Initialize and
close that engine according to its lifecycle; closing `LearningEngine` does not
close an injected engine. A standalone `LearningEngine` that uses the default
retriever should be closed with `await learning.aclose()`. `FeedbackManager`
closes the retrieval engine it creates when `await manager.aclose()` is called
and can recreate it if the manager is used again.

## Scope and evidence

The authenticated tenant and application namespace map to RetrievalLoom's
`Scope`. Artifact visibility, agent, task, tool and workflow filters are applied
before retrieval. Each returned hit is then checked against the authoritative
artifact repository for current version, status, scope, applicability,
assessment and review policy. A stale projection can therefore be skipped but
cannot become returned guidance.

`RetrievedArtifact.engine_evidence` preserves RetrievalLoom evidence, including
source references, stage scores, retrieval hits and raw method scores.
`RetrievalResult.retrieval_snapshot` and `retrieval_warnings` retain the
engine's snapshot and warning details. `as_context()` still renders only the
artifact title and content as untrusted context.

Retrieval requires a nonblank task. Candidate count, result count and rendered
context are bounded by `RetrievalRequest`. A repository mutation invalidates the
affected in-process projection; the next search rebuilds it from authoritative
records before retrieval.
Call `RetrievalLoomRetriever.reindex(scope)` to rebuild a namespace after
restart or out-of-band repository changes.

When several processes share a durable artifact repository, each owned engine is
still process-local, and another process may consume the projection outbox. The
retriever therefore rebuilds a scope's owned projection once `projection_ttl`
(default 30 seconds) has elapsed since its last rebuild started. A newly
activated artifact from another process can be missing for at most that long. A
deprecated, superseded or changed one is never returned, because every hit is
revalidated. Pass `projection_ttl=None` for a single-process host. Each refresh
reads the scope's active artifacts, so lengthen the TTL for very large scopes.
Repositories that are not durable cannot be shared between processes and never
expire. For a caller-owned engine, follow RetrievalLoom's multiple-engine
guidance instead: share its persistent metadata store and run its lifecycle
rounds.

## Components and storage

Use RetrievalLoom's native `Components` and `EngineResources` when supplying
custom keyword, fuzzy, sparse, vector, graph or structure components. The
Feedback Manager adapter does not take ownership of injected components.
Applications should configure persistent metadata and lifecycle recovery in
RetrievalLoom for durable indexes; the built-in manager defaults are
process-local and are not a durable learning guarantee.

The legacy `ArtifactIndexer`, `VectorRetriever`, `GraphRetriever` and
`HybridRetriever` classes are deprecated and emit `DeprecationWarning` when
constructed. They remain importable for existing applications and will be
removed in a future major release. Migrate to `RetrievalLoomRetriever`, which
keeps RetrievalLoom as the single retrieval and evidence engine:

| Legacy usage | Replacement |
|---|---|
| `ArtifactIndexer(...).index(...)` | `RetrievalLoomRetriever.reindex(scope)`; repository mutations are projected automatically |
| `VectorRetriever(...)` | RetrievalLoom's dense channel through an injected embedding component |
| `GraphRetriever(...)` or `HybridRetriever(graph=...)` | RetrievalLoom's graph channel (below) |
| `HybridRetriever(...).retrieve(request)` | `RetrievalLoomRetriever(repository).retrieve(request)` |

## Graph expansion depth

`RetrievalRequest.graph_depth` is honored through RetrievalLoom's native graph
channel; it is never silently ignored. Configure the depth once:

- `RetrievalLoomRetriever(repository, graph_depth=2)` enables the graph channel
  at depth 2 on the engine the retriever owns.
- With a caller-owned engine, configure RetrievalLoom's `GraphChannelConfig`
  yourself; passing `graph_depth` as well raises `ValueError`.

A request with `graph_depth=0` disables graph expansion for that request. A
nonzero request depth must equal the engine's configured depth. Otherwise, or
when the engine has no graph channel, retrieval raises
`UnsupportedCapabilityError` instead of returning results without graph expansion.

## Optional local BGE-M3 embeddings

The default engine does not download or load a model. For dense retrieval with
the locally copied BGE-M3 checkpoint, install the source-repository model group
and run [example 17](https://github.com/smuniharish/feedback-manager/blob/master/examples/17_local_bge_m3.py):

```bash
uv sync --group examples --group models
uv run python examples/17_local_bge_m3.py
```

The example loads `models/bge-m3` through FlagEmbedding's
`BGEM3FlagModel` and RetrievalLoom's native embedding component contract. It
forces Hugging Face offline mode, uses the checkpoint on disk, and does not
download weights. The ignored `models/` directory is local development data;
the model weights are not part of the package.
