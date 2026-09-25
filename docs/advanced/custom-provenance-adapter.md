# Provenance: `langgraph-xai` only

Unlike `FeedbackStore`, `FeedbackHandler`, `FeedbackRouter`, and the
policy hooks, provenance is **not** a generic, pluggable extension point.
`langgraph-xai` is a mandatory runtime dependency and the sole supported
provenance source, so `FeedbackManager` depends directly on
`XAIProvenanceAdapter` rather than on a generic `Protocol`/`ABC` contract.

Pass your `XAIRuntime` straight to `FeedbackManager` via `xai_runtime` --
it builds the `XAIProvenanceAdapter` for you automatically:

```python
from langgraph_xai import XAIRuntime

from feedback_manager import FeedbackManager

runtime = XAIRuntime(application_id="support-bot", tenant_id="acme-corp", graph_id="qa-graph")
manager = FeedbackManager(xai_runtime=runtime)
```

If you already hold a constructed `XAIProvenanceAdapter` (for example in
tests), pass it directly via `provenance_adapter` instead; the two
parameters are mutually exclusive.

See [Provenance model](../architecture/PROVENANCE_MODEL.md) for how
`XAIProvenanceAdapter` resolves provenance live (during a run) or
after the fact (via `ProvenanceStore`, by `run_id`).

