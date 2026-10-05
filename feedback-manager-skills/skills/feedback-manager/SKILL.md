---
name: feedback-manager
description: Integrate, configure, test, and debug feedback-manager, the feedback infrastructure library for LangChain and LangGraph applications. Use when an application must capture, store, route, query, or resolve feedback - human corrections, ratings, approvals, evaluator scores, tool or model failures, interruptions - or record LangGraph human-in-the-loop requests and decisions, link feedback to langgraph-xai provenance, implement a FeedbackStore for a database, write handlers, subscribers, or policies, or test any of these. Also use when feedback is missing, duplicated, rejected with validation or lifecycle errors, or has no provenance. Do not use to build a tracing platform, an evaluation framework, or a UI.
license: Apache-2.0
compatibility: Python 3.12 or newer with LangGraph 1.x. Installs feedback-manager 0.1.x from PyPI, which brings langgraph-xai 1.x. scripts/verify_setup.py runs offline in the project's Python environment.
metadata:
  version: "0.1.1"
  documentation: "https://feedback-manager.readthedocs.io"
---

# feedback-manager

`feedback-manager` makes feedback a first-class record in LangChain and
LangGraph applications. Each piece of feedback is a validated, immutable
`FeedbackEvent` that says who sent it (source), what kind it is (category),
what it is about (target), and where in an execution it happened (execution
context). `FeedbackManager` correlates, stores, publishes, and routes it, then
moves it through an explicit lifecycle until it is resolved.

Use this skill to add the existing package to an application. LangGraph and
LangChain keep owning execution, interrupts, and callbacks; `langgraph-xai`
owns provenance. Do not reimplement any of them.

## Before you change anything

1. From the project's Python environment, run the setup check in this skill's
   directory:

   ```bash
   python scripts/verify_setup.py
   ```

   It checks Python and package versions and runs feedback, failure capture,
   human-in-the-loop, and provenance flows offline. Fix every `FAIL` line first.
2. Find where feedback originates: user-facing endpoints, graph nodes,
   tools, evaluators, and human-in-the-loop pauses. Find any existing
   `FeedbackManager` and the tests that cover it.
3. Pick the matching recipe in [references/RECIPES.md](references/RECIPES.md).
   Look up exact signatures in [references/API.md](references/API.md).

## Core workflow

1. Install the package:

   ```bash
   pip install feedback-manager
   ```

2. Create one `FeedbackManager` per application or tenant boundary. Every
   argument is optional; the defaults keep feedback in memory. Pass a durable
   `store` in production.

   ```python
   from feedback_manager import FeedbackManager

   manager = FeedbackManager()
   ```

3. Submit feedback where it originates, with the execution it is about:

   ```python
   from feedback_manager import FeedbackTarget, FeedbackTargetType
   from feedback_manager.integrations.langgraph import execution_context_from_config

   feedback = await manager.submit(
       source="human",
       category="correction",
       target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id=generation_id),
       payload={"corrected_text": text},
       execution_context=execution_context_from_config(config),
       idempotency_key=f"correction:{generation_id}:{user_id}",
   )
   ```

4. Capture failures automatically by adding the callback handler to the
   run's callbacks:

   ```python
   from feedback_manager.integrations.langchain import FeedbackCallbackHandler

   await graph.ainvoke(inputs, {"callbacks": [FeedbackCallbackHandler(manager)]})
   ```

5. Route new feedback to handlers, and move it through the lifecycle as
   people work on it: `acknowledge`, `mark_handled`, then `resolve`, or
   `reject`, `cancel`, `expire`.
6. Test with a strict manager, using
   [assets/test_feedback_integration.py](assets/test_feedback_integration.py),
   then run the project's tests and `python scripts/verify_setup.py` again.

## Rules

- **Never change how graphs execute.** Feedback capture observes; it does not
  alter inputs, outputs, state, or control flow.
- **Use LangGraph for pauses.** Pause with LangGraph's `interrupt()`, record
  the request with `HumanInTheLoopBridge.request` after the graph paused, and
  resume with `bridge.resume_command(response)`. Never call `request` inside
  the node: the node re-runs on resume and records the request twice.
- **Submit each piece of feedback once.** Pass an `idempotency_key` derived
  from what makes the feedback unique whenever a submission can be retried.
- **Change status only through the lifecycle methods.** Events are frozen.
  Never write statuses into a store directly, and never skip states:
  `resolve` requires `HANDLED`.
- **Keep payloads JSON and free of secrets.** Payloads, metadata, and
  resolutions are JSON objects with finite numbers. Add a
  `FeedbackRedactionPolicy` for personal data; keep personal data out of IDs.
- **Build execution context with the helpers.** Use
  `execution_context_from_config(config)` in nodes and tools and
  `execution_context_from_snapshot(snapshot)` after a pause, so feedback
  carries the `langgraph-xai` run ID.
- **Implement the whole store contract.** A custom `FeedbackStore` makes
  `transition` a compare-and-set on `expected`, deduplicates by
  `idempotency_key` in `create`, and raises the package's errors.
- **Choose failure modes on purpose.** Production defaults are best-effort and
  emit `feedback.failed`; tests use `BLOCKING` for every stage.
- **Catch specific errors.** Every error derives from `FeedbackManagerError`;
  handle `FeedbackValidationError`, `FeedbackLifecycleError`, or
  `FeedbackStoreError` where they matter instead of swallowing all of them.

## Where things go

- Events go to the configured `FeedbackStore`, by default
  `InMemoryFeedbackStore`, which loses them on exit.
- New events go to the router's handlers, once. Every new event and every
  lifecycle change goes to subscribers and streams in the same process.
- Observability events (`feedback.received`, `feedback.failed`, and others)
  go to the `ObservabilitySink`, by default structured log records.
- With `FeedbackManager(xai_runtime=xai)`, each event gets a provenance
  reference to the `langgraph-xai` run it is about. Decision and evidence IDs
  exist only for feedback submitted while the run is active.

## When something is wrong

Follow [references/TROUBLESHOOTING.md](references/TROUBLESHOOTING.md).
Common causes:

- `FeedbackValidationError`: a payload value is not JSON, a number is not
  finite, or an identifier is empty or padded.
- `FeedbackLifecycleError`: an illegal move, such as `resolve` before
  `mark_handled`, or a change to closed feedback.
- `provenance` is `None`: no `xai_runtime`, no `langgraph-xai` run ID in the
  execution context, or a graph instrumented by another runtime.
- Nothing recorded by the callback handler: it is not in the run's
  `callbacks`, or the run succeeded; only failures are recorded.

## References

- [references/API.md](references/API.md): public imports, methods, models,
  contracts, and errors.
- [references/RECIPES.md](references/RECIPES.md): complete patterns for common
  integration tasks.
- [references/TROUBLESHOOTING.md](references/TROUBLESHOOTING.md): symptoms,
  causes, and fixes.
- Documentation: <https://feedback-manager.readthedocs.io>
