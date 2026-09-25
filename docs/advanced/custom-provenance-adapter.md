# Provenance: `langgraph-xai` only

Unlike `FeedbackStore`, `FeedbackHandler`, `FeedbackRouter`, and the
policy hooks, provenance is **not** a generic, pluggable extension point.
`langgraph-xai` is a mandatory runtime dependency and the sole supported
provenance source, so `FeedbackManager` depends directly on
`XAIProvenanceAdapter` rather than on a generic `Protocol`/`ABC` contract.

```python
from langgraph_xai import XAIRuntime

from feedback_manager import FeedbackManager
from feedback_manager.integrations.xai import XAIProvenanceAdapter

runtime = XAIRuntime(application_id="support-bot", tenant_id="acme-corp", graph_id="qa-graph")
manager = FeedbackManager(provenance_adapter=XAIProvenanceAdapter(runtime))
```

See [Provenance model](../architecture/PROVENANCE_MODEL.md) for how
`XAIProvenanceAdapter` resolves provenance live (during a run) or
after the fact (via `ProvenanceStore`, by `run_id`).

