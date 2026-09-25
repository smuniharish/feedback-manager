# Correlation Model

Feedback correlation is defined by:

- `ExecutionContext` in `core/context.py`
- `CorrelationContext` in `core/context.py`
- `FeedbackCorrelator` protocol in `contracts/correlator.py`
- `DefaultFeedbackCorrelator` in `correlation/correlator.py`

## Execution context

`ExecutionContext` carries execution identifiers without importing framework internals. It can hold:

- application, tenant, and graph identifiers
- thread, run, checkpoint, node, and task identifiers
- message, tool call, and generation identifiers
- free-form metadata

The context may be partial. For example:

- a human correction may only know `generation_id`
- a callback error may know `run_id` and `tool_call_id`
- a UI-entered comment may have no execution context at all

## Correlation context

`CorrelationContext` is the derived link layer used by the feedback system itself:

- `correlation_id`
- `parent_feedback_id`
- `related_feedback_ids`
- `execution`
- `metadata`

The bundled correlator currently fills:

- `correlation_id`
- `execution`

The other fields exist so custom correlators can represent parent/child or related feedback structures.

## Default correlator behavior

`DefaultFeedbackCorrelator.correlate()` derives a `correlation_id` from the available execution context:

1. use `run_id` if present
2. else use `thread_id`
3. else use `checkpoint_id`
4. else generate a random UUID string

This means:

- feedback about the same run can be queried together
- feedback about the same thread can still group together when no run id exists
- feedback remains usable even with no execution context

## Submission flow

During `FeedbackManager.submit()`:

1. the caller may provide an `ExecutionContext`
2. the correlator is invoked with the pending `FeedbackEvent` and that context
3. the returned `CorrelationContext` is attached to the event
4. the event is then persisted

## Querying implication

Because `FeedbackQuery` supports `correlation_id`, applications can retrieve related feedback across sources and categories for the same execution unit.

## LangGraph boundary

`integrations/langgraph/adapter.py` provides `execution_context_from_config()`, which maps `RunnableConfig` data into an `ExecutionContext`. This is a translation step only; the core correlation model remains framework-independent.

