# Correlation

Correlation lets you group feedback around the same execution unit.

Two models are involved:

- `ExecutionContext` — raw identifiers such as `run_id`, `thread_id`, `node_id`, `tool_call_id`
- `CorrelationContext` — derived feedback-side linking information, including `correlation_id`

Default behavior:

1. prefer `run_id`
2. else `thread_id`
3. else `checkpoint_id`
4. else generate a random UUID string

This means the same run can accumulate:

- human corrections
- tool failures
- evaluator scores
- approval decisions

and they can still be queried together by `correlation_id`.

Deep dive: [Correlation architecture](../architecture/CORRELATION_MODEL.md).

