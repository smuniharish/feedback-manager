# Provenance Model

The public provenance boundary consists of:

- `FeedbackProvenanceReference` on feedback records
- the `xai_runtime` configuration accepted by `FeedbackManager`

## Core idea

`feedback-manager` does not capture provenance itself. It consumes provenance
from `langgraph-xai` and represents it as a small framework-independent
reference object. `langgraph-xai` is the mandatory and only supported
provenance source. Applications pass an `XAIRuntime` to `FeedbackManager`;
the translation mechanism is not a public extension point.

## Resolution strategy

Provenance can be resolved through two paths.

### 1. Live, in-run resolution

If the supplied runtime exposes a current run on the calling task, feedback
submitted during that run can be correlated immediately.

This is the path used when feedback is submitted **from inside a running graph node during execution**.

### 2. Post-run fallback

If there is no active run, the feedback execution context can supply a
`run_id` for post-run lookup through `langgraph-xai`.

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

then provenance remains `None`.

## Mapped fields

The resulting `FeedbackProvenanceReference` can contain:

- `execution_id`
- `tool_execution_id` when `tool_call_id` matches
- `human_interaction_id` from the latest human interaction, when present
- `summary`
- metadata including node count, tool count, human interaction count, and execution status

It also attempts targeted matching using:

- `execution_context.tool_call_id`
- `execution_context.node_id`

## Important boundary rule

FeedbackManager translates provenance references; it does not own provenance
persistence, run storage, or capture instrumentation. Those remain the job
of `langgraph-xai`.
