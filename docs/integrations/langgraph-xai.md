# `langgraph-xai` integration

File:

- `integrations/xai/adapter.py`

## Purpose

`XAIProvenanceAdapter` resolves `FeedbackProvenanceReference` objects from a real `XAIRuntime`.

## Resolution order

1. use `runtime.current_run` when feedback is submitted during an active run
2. otherwise use `correlation.execution.run_id`
3. look up a registered `ProvenanceStore` via `runtime.registry.get(ProvenanceStore)`

## Example

```python
from langgraph_xai import XAIRuntime

from feedback_manager import FeedbackManager
from feedback_manager.integrations.xai import XAIProvenanceAdapter

runtime = XAIRuntime(
    application_id="support-bot",
    tenant_id="acme-corp",
    graph_id="qa-graph",
)
manager = FeedbackManager(provenance_adapter=XAIProvenanceAdapter(runtime))
```

When a graph instrumented by that runtime calls `manager.submit(...)` inside a node, provenance can be attached immediately.

See `examples/06_provenance.py` and `tests/integration/test_xai_provenance.py`.

