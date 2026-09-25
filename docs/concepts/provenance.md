# Provenance

Provenance answers: **which execution produced the thing this feedback is
about?**

`feedback-manager` does not capture execution provenance itself.
`langgraph-xai` is the mandatory and only provenance source. Provenance is
therefore not a pluggable extension point: applications do not implement or
select a provenance adapter.

## Enable provenance

Create the `XAIRuntime` used to instrument your graph and pass that runtime
directly to `FeedbackManager`:

```python
from langgraph_xai import XAIRuntime

from feedback_manager import FeedbackManager

runtime = XAIRuntime(
    application_id="support-bot",
    tenant_id="acme-corp",
    graph_id="qa-graph",
)
manager = FeedbackManager(xai_runtime=runtime)
```

When feedback is submitted during an execution instrumented by that runtime,
the resulting `FeedbackEvent.provenance` can identify the related execution,
decision, evidence, human interaction, or tool execution. If no matching
execution context is available, provenance remains `None`; feedback
submission still succeeds unless your configured failure policy says
otherwise.

Consumers should configure `xai_runtime`; the concrete translation machinery
is an implementation detail.

See [Provenance example](../examples/provenance.md) for the complete runnable
graph and verified output. For ownership and data-flow details, see
[Provenance architecture](../architecture/PROVENANCE_MODEL.md).
