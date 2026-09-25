# Provenance adapter: `langgraph-xai` only (not a customization point)

Unlike `FeedbackStore`, `FeedbackHandler`, `FeedbackRouter`, and the
policy hooks -- which genuinely are extension points, implemented against
an `ABC`/`Protocol` so applications can plug in their own -- provenance is
**deliberately not pluggable**. `langgraph-xai` is a mandatory runtime
dependency and the *only* supported provenance source. There is no
`FeedbackProvenanceAdapter` `ABC`/`Protocol` to implement against and no
extension point here: `FeedbackManager` depends directly on the concrete
`XAIProvenanceAdapter` class.

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

