# feedback-manager troubleshooting

Find the symptom, check the causes in order, and apply the fix.

## Submission

| Symptom | Cause | Fix |
|---|---|---|
| `FeedbackValidationError: ... input was not a valid JSON value` | A payload, metadata, or resolution value is not JSON, such as a `datetime`, `UUID`, or object. | Convert it first: `value.isoformat()`, `str(value)`, or a plain dict. |
| `FeedbackValidationError: ... Input should be a finite number` | `NaN` or `Infinity` in a payload. | Use `None` or a string. |
| `ValidationError: ... must not start or end with whitespace` or `must be a non-empty string` | A padded or empty identifier, source, category, or target type. | Strip and check identifiers before building models. |
| A retried request created two events | No `idempotency_key`, or a different one per attempt. | Derive the key from what makes the feedback unique and reuse it on retry. |
| `FeedbackConfigurationError: store must be a FeedbackStore` | A collaborator of the wrong type was passed to `FeedbackManager`. | Subclass the matching contract; see the API reference. |
| `FeedbackStoreError` | The store raised; the original error is `__cause__`. | Fix the store or its connection. Nothing was stored. |

## Lifecycle

| Symptom | Cause | Fix |
|---|---|---|
| `FeedbackLifecycleError: illegal feedback lifecycle transition received -> resolved` | `resolve` requires `HANDLED`. | Call `acknowledge` and `mark_handled` first, or use `reject` or `cancel`. |
| `FeedbackLifecycleError: feedback is resolved (terminal) ...` | Closed feedback cannot change. | Submit new feedback instead. Repeating the closing call itself is allowed. |
| A lifecycle call returned the event but subscribers saw nothing | The event was already in that status: the call was a no-op. | Expected; idempotent calls publish nothing. |
| `FeedbackConflictError` reaches application code | A custom store reports conflicts for moves that applied, or compares with the wrong status. | Make `transition` compare the stored status with `expected` atomically. |
| `HANDLED` feedback does not expire | By design: handled feedback awaits its resolution. | Resolve, reject, or cancel it. |

## Failure capture

| Symptom | Cause | Fix |
|---|---|---|
| `FeedbackCallbackHandler` records nothing | It is not in the run's `callbacks`, or the run succeeded. | Pass it in `config["callbacks"]`. Only failures are recorded. |
| One cancellation for a run whose nodes were all cancelled | Cancelling a run cancels everything inside it. | Expected: a cancellation is recorded once, about the top-level run. |
| A `GraphInterrupt` is not recorded | Interrupts are control flow, not failures. | Record pauses with `HumanInTheLoopBridge`. |
| A direct MCP or HTTP tool failure is missing | No LangChain runnable, so no callbacks. | Wrap the call in `capture_tool_feedback`. |
| The callback handler logs `Error in FeedbackCallbackHandler...` | Recording failed, for example the store is down. | Fix the store; the original exception still propagated. |

## Human-in-the-loop

| Symptom | Cause | Fix |
|---|---|---|
| Each request is recorded twice | `bridge.request` runs inside the node, which re-runs on resume. | Call it after `ainvoke` returns with `__interrupt__`. |
| `extract_interrupts` returns nothing | The graph did not pause, or it has no checkpointer. | Compile with a checkpointer and pass a `thread_id`. |
| `FeedbackValidationError: request() needs an interrupt or a prompt` | Neither was passed. | Pass the pending `interrupt` from `extract_interrupts`. |
| `FeedbackLifecycleError` from `bridge.resolve` | The request was already closed with a different decision. | Resolve each request once; repeating the same decision is allowed. |

## Provenance

| Symptom | Cause | Fix |
|---|---|---|
| `provenance` is `None` | No `xai_runtime` on the manager, or the graph is instrumented by another runtime. | Share one `XAIRuntime` between `instrument` and `FeedbackManager`. |
| `provenance` is `None` for feedback from a node | The execution context has no run ID. | Use `execution_context_from_config(config)`, with `config: RunnableConfig` in the node signature. |
| `provenance` is `None` after the run | The `run_id` is not a `langgraph-xai` run ID, or the run is not in the provenance store. | Use the run ID from `xai.collect_runs()` or `execution_context_from_snapshot`. |
| No `node_execution_id` for in-node feedback | Node executions are recorded when nodes finish. | Expected; feedback from a later node or after the run has it. |
| No `decision_id` or `evidence_ids` after the run | `langgraph-xai` keeps them in the live run only. | Submit decision feedback during the run. |
| A `feedback.failed` event with `stage="provenance"` | The provenance lookup raised. | Check the provenance store; the feedback was stored without provenance. |

## Delivery and operations

| Symptom | Cause | Fix |
|---|---|---|
| `FeedbackConfigurationError: stream() must be called from a running event loop` | `stream()` was called from synchronous code. | Open streams inside `async` code. |
| A stream receives nothing | It opened after the change, its query does not match, or it is read on another loop. | Open it first, check the query, and consume it on its own loop. |
| Logs show `feedback stage failed; continuing` | A best-effort stage raised; the log names the `stage`. | Fix the component, or make the stage `BLOCKING` to surface it. |
| `TypeError: ... not a FeedbackHandlerResult` (or `not a FeedbackHandler`) | A handler returned something else, or a router selected a non-handler. | Return `FeedbackHandlerResult(handled=...)`; route to `FeedbackHandler` instances. |
| Memory grows with an open stream | Undelivered events are buffered without limit. | Read streams continuously and close them when done. |
| `Psycopg cannot use the 'ProactorEventLoop'` | psycopg async needs a selector loop on Windows. | `asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)`. |
