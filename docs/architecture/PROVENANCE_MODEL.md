# Provenance Model

The provenance boundary is implemented by:

- `FeedbackProvenanceReference` in `core/provenance.py`
- `XAIProvenanceAdapter` in `integrations/xai/adapter.py`

## Core idea

`feedback-manager` does not capture provenance itself. It consumes provenance from `langgraph-xai` and translates it into a small framework-independent reference object. `langgraph-xai` is the mandatory, default, and only supported provenance source -- `FeedbackManager` takes an optional `provenance_adapter: XAIProvenanceAdapter | None` and depends on it directly, rather than through a generic pluggable contract, since there is exactly one provenance provider by design.

## Bundled adapter: `XAIProvenanceAdapter`

`XAIProvenanceAdapter` is the only place in this package that imports `langgraph_xai` types.

It imports:

- `Execution`
- `ProvenanceStore`
- `XAIRuntime`

## Resolution strategy

`resolve(correlation)` uses two paths.

### 1. Live, in-run resolution

First it checks:

- `self._runtime.current_run`

If a current run exists on the calling task, the adapter uses that run's `execution` directly.

This is the path used when feedback is submitted **from inside a running graph node during execution**.

### 2. Post-run fallback

If there is no active current run, the adapter looks for:

- `correlation.execution.run_id`

If a `run_id` exists, it then asks the runtime registry for a provenance store:

- `self._runtime.registry.get(ProvenanceStore)`

If a store is registered, it loads the execution by run id.

This is the post-run path used when feedback is submitted or reconciled **after the run has already finished**.

## What can and cannot be captured

### Can be captured live

If you call `manager.submit(...)` inside an instrumented graph node while the run is active:

- `runtime.current_run` is available
- provenance can be attached immediately

### Can be resolved after the fact

If you later know the run id and the runtime has a registered `ProvenanceStore`:

- provenance can be resolved after the run by `run_id`

### Cannot be inferred magically

If feedback is created:

- outside graph execution, and
- without an execution `run_id`, and
- without a live `current_run`

then the bundled adapter returns `None`.

## Mapped fields

`XAIProvenanceAdapter._map_execution()` currently populates:

- `execution_id`
- `tool_execution_id` when `tool_call_id` matches
- `human_interaction_id` from the latest human interaction, when present
- `summary`
- metadata including node count, tool count, human interaction count, and execution status

It also attempts targeted matching using:

- `execution_context.tool_call_id`
- `execution_context.node_id`

## Important boundary rule

The adapter translates provenance; it does not own provenance persistence, run storage, or capture instrumentation. Those remain the job of `langgraph-xai`.

