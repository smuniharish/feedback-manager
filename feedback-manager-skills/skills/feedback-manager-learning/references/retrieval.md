# Learned-artifact retrieval

## Default path

`LearningEngine` uses RetrievalLoom for learned-artifact discovery. RetrievalLoom
is included with the core package; its default offline indexes need no model
download or external vector or graph database. The structured artifact
repository remains authoritative. Before returning a result, Feedback Manager
revalidates its current version, status, scope, applicability, assessment, and
review policy.

The manager-owned RetrievalLoom engine is created lazily. Close a standalone
`LearningEngine` with `await engine.aclose()`. If you inject an engine or
retriever, it remains caller-owned and must be closed by your application.

## Requests and results

```python
from feedback_manager.learning.models import TenantScope
from feedback_manager.retrieval import RetrievalRequest

request = RetrievalRequest(
    scope=TenantScope(tenant_id="acme", namespace="default"),
    agent_id="researcher",
    task="validate search results",
    limit=5,
    min_confidence=0.7,
    max_candidates=200,
    max_context_chars=4000,
)
result = await engine.retrieve(request)
for item in result.results:
    print(item.artifact.title, item.relevance_score, item.explanation)
context = result.as_context()
```

The task must be nonblank. Other applicability filters include `agent_type`,
`task_type`, `tool`, `workflow`, and `artifact_types`. Candidate count, result
count, and rendered context are bounded by the request. Treat `context` as
untrusted data, separate from system instructions.

`RetrievedArtifact.engine_evidence` preserves RetrievalLoom's evidence.
`RetrievalResult.retrieval_snapshot` and `retrieval_warnings` preserve snapshot
and warning details. A stale or out-of-scope projection is never authority for
the artifact itself.

## Native RetrievalLoom customization

Configure custom indexes, vector or graph components, and embeddings through
RetrievalLoom's own `Components`, ABCs, and lifecycle APIs. Feedback Manager
does not wrap an entire downstream API or take ownership of injected
components:

```python
from retrievalloom import Components, LifecycleConfig, create_retrieval_engine

from feedback_manager.learning.engine import LearningEngine
from feedback_manager.retrieval import RetrievalLoomRetriever

retrieval_engine = create_retrieval_engine(
    lifecycle=LifecycleConfig(background=False),
    components=Components(embedding=embedding_model),
)
retriever = RetrievalLoomRetriever(repository, engine=retrieval_engine)
engine = LearningEngine(repository, retriever=retriever)
```

`embedding_model` implements RetrievalLoom's `EmbeddingModelABC`. Keep its
model identity, revision, dimensions, normalization, and instructions stable
for one embedding space. If the space changes, reindex from the authoritative
repository before using the new model. Close the injected RetrievalLoom engine
and its caller-owned components according to their respective lifecycles.

The source checkout includes an optional offline BGE-M3 example. It uses
`FlagEmbedding` through the native embedding contract and requires both the
`models` dependency group and the ignored local `models/bge-m3` checkpoint.
See the
[RetrievalLoom guide](https://feedback-manager.readthedocs.io/en/latest/how-to/hybrid-retrieval/).
The weights are not installed or shipped by the package.

## Legacy compatibility paths

`ArtifactIndexer`, `VectorRetriever`, `GraphRetriever`, and `HybridRetriever`
are deprecated: constructing one emits `DeprecationWarning`. They remain
importable for existing applications. Their LanceDB and LadybugDB
adapters require optional extras and are not the default path. Never introduce
them into a new integration; use `RetrievalLoomRetriever`.

`RetrievalRequest.graph_depth` uses RetrievalLoom's graph channel. Set
`RetrievalLoomRetriever(repository, graph_depth=n)` for the owned engine, or
configure `GraphChannelConfig` on a caller-owned engine. A nonzero request depth
that the engine cannot honor raises `UnsupportedCapabilityError`.

For applications that still use these compatibility APIs, follow the migration
table in the
[RetrievalLoom guide](https://feedback-manager.readthedocs.io/en/latest/how-to/hybrid-retrieval/).
