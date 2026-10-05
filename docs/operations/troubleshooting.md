# Troubleshooting

## Submitting feedback

??? question "`FeedbackValidationError: invalid feedback: payload.when: input was not a valid JSON value`"
    Payloads, metadata, and resolutions must be JSON. Convert values such as
    `datetime`, `UUID`, `Decimal`, or custom objects first, for example with
    `value.isoformat()` or `str(value)`.

??? question "`FeedbackValidationError: ... Input should be a finite number`"
    `NaN` and `Infinity` are rejected, because JSON cannot represent them. Store
    `None` or a sentinel string instead.

??? question "`ValidationError: ... must not start or end with whitespace`"
    Identifiers (target IDs, context IDs, idempotency keys) must be non-empty
    and unpadded. Strip user-provided IDs before building a `FeedbackTarget`.

??? question "A retried submission created a second event"
    Only submissions with the same `idempotency_key` are deduplicated. Derive
    the key from what makes the feedback unique in your application, such as
    `f"rating:{generation_id}:{user_id}"`, and pass it on every attempt.

## Lifecycle

??? question "`FeedbackLifecycleError: illegal feedback lifecycle transition received -> resolved`"
    Feedback is resolved from `HANDLED`. Call `acknowledge` and `mark_handled`
    first, or use `reject` or `cancel`, which are allowed earlier. The
    [lifecycle](../concepts/lifecycle.md#legal-moves) lists every legal move.

??? question "`FeedbackLifecycleError: feedback is resolved (terminal) and cannot move to cancelled`"
    Closed feedback stays closed. Repeating the call that closed it is allowed
    and returns it unchanged.

??? question "`FeedbackConflictError` reaches my code"
    The manager retries conflicts itself. If one reaches you, your store
    probably reports conflicts for moves that did apply, or compares against the
    wrong status. Check `transition` against the
    [store contract](../concepts/storage.md#the-store-contract).

## Capturing failures

??? question "`FeedbackCallbackHandler` records nothing"
    - Pass the handler in the run's `callbacks`, for example
      `graph.ainvoke(inputs, {"callbacks": [handler]})`.
    - Successful runs are not recorded: only failures are.
    - LangGraph interrupts and other control flow are not failures.
    - Failures that your code or a `ToolNode` catches are still recorded,
      because LangChain reports them before they are caught.
    - Cancellations are recorded once, for the top-level run that was
      cancelled; the runs inside it are not
      ([cancellations and timeouts](../how-to/langchain-failures.md#cancellations-and-timeouts)).

??? question "`TypeError: ... returned bool, not a FeedbackHandlerResult` in a `feedback.failed` event"
    A handler's `handle` must return a `FeedbackHandlerResult`, and a router's
    `route` a sequence of `FeedbackHandler` instances. Return
    `FeedbackHandlerResult(handled=False)` for a deliberate no-op.

??? question "A tool failure is not recorded for a direct MCP call"
    Calls that do not go through a LangChain runnable produce no callbacks.
    Wrap them in [`capture_tool_feedback`](../how-to/langchain-failures.md#tool-calls-outside-langchain).

??? question "Each human-in-the-loop request is recorded twice"
    `bridge.request` is called inside the node, before `interrupt()`. LangGraph
    runs the node again from the start when it resumes. Record the request after
    the graph paused, outside the graph.

## Provenance

??? question "`feedback.provenance` is `None`"
    - The manager has no `xai_runtime`, or the graph is instrumented by a
      different runtime.
    - The feedback has no `run_id` and was submitted outside an instrumented
      call. Build its context with `execution_context_from_config(config)` or
      `execution_context_from_snapshot(snapshot)`.
    - The `run_id` is not a `langgraph-xai` run ID, for example a LangChain run
      ID, or it names a run that is not in the runtime's provenance store.
    - The lookup failed: look for a `feedback.failed` event with
      `stage="provenance"`.

??? question "`node_execution_id` is missing for feedback submitted inside a node"
    `langgraph-xai` records a node execution when the node finishes. Feedback
    submitted from inside the node, before it returns, cannot point at it yet.

??? question "`decision_id` and `evidence_ids` are empty after the run"
    `langgraph-xai` does not store decisions and evidence; they are available
    only while the run is active. Submit feedback about a decision during the
    run to capture them.

## Delivery

??? question "`FeedbackConfigurationError: stream() must be called from a running event loop`"
    Open streams inside `async` code. A stream delivers to the event loop that
    opened it.

??? question "A stream receives nothing"
    Streams deliver changes made after they were opened, matching their query.
    Make sure the stream was open before the change, that its query matches,
    and that you consume it on the loop that opened it.

??? question "Logs show `feedback stage failed; continuing`"
    A handler, subscriber, router, correlator, or provenance lookup raised, and
    its failure mode is best-effort. The record names the `stage` and includes
    the traceback. Fix the failing component, or make the stage
    [blocking](../how-to/failure-isolation.md) to surface the error.

## Examples and infrastructure

??? question "`Psycopg cannot use the 'ProactorEventLoop' to run in async mode`"
    psycopg's async mode needs a selector event loop, which is not the default
    on Windows. Run your entry point with
    `asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)`; the example
    store's `run()` helper does this.

??? question "Connection refused on `localhost:5432` or `localhost:3000`"
    Some container setups, such as a Podman machine on Windows, do not forward
    `localhost`. Use the container machine's IP address in
    `FEEDBACK_MANAGER_POSTGRES_DSN` and `FEEDBACK_MANAGER_GRAFANA_URL`.

??? question "An agent example exits with `Set FEEDBACK_MANAGER_EXAMPLE_MODEL ...`"
    Examples 07 and 08 need a chat model; see the
    [agent prerequisites](../examples/agents.md#prerequisites).
