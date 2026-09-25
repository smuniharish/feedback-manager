---
name: feedback-manager
description: Integrate, configure, debug, test, or extend the feedback-manager Python library for LangChain/LangGraph applications that need to capture, correlate, persist, route, and resolve human corrections, HITL approvals, tool failures, evaluator scores, generation interruptions, or provenance-linked feedback. Use when adding a durable, first-class feedback domain model around an existing agent application without inventing a duplicate feedback/event system.
---

# feedback-manager

Use this skill for the existing `feedback-manager` Python package, not to
create a new feedback model, event bus, ticketing system, or agent runtime.

`feedback-manager` is a **library**, not an agent framework or runtime. It
does not execute agents, orchestrate graphs, replace LangGraph interrupts,
checkpoints, or streaming, implement evaluators, or perform self-improvement.
LangChain, LangGraph, `langgraph-xai`, and the host application keep those
responsibilities.

The stable public surface most applications need:

```python
from feedback_manager import (
    FeedbackManager,
    FeedbackSource,
    FeedbackCategory,
    FeedbackTarget,
    FeedbackTargetType,
    ExecutionContext,
    FeedbackQuery,
)
```

Read [`references/architecture.md`](references/architecture.md) before
reasoning about internal behavior. Read
[`references/integration.md`](references/integration.md) before adding it to
an application. Read [`references/extensibility.md`](references/extensibility.md)
before replacing a store, router, handler, or policy. Read
[`references/troubleshooting.md`](references/troubleshooting.md) before
debugging a failure.

## Activate when

Use `feedback-manager` when a LangChain/LangGraph application needs a durable,
queryable, first-class record of feedback rather than ad hoc logs, UI
comments, or one-off tables.

Typical indicators:

- capturing human corrections to generated answers;
- recording approve/reject decisions around a LangGraph human-in-the-loop
  interrupt;
- turning tool failures, timeouts, or cancellations into queryable records;
- recording evaluator/LLM-as-judge scores or critiques tied to a generation;
- recording generation interruptions or partial results;
- correlating any of the above to a run, thread, checkpoint, node, tool call,
  or generation, optionally with `langgraph-xai` provenance;
- an existing `feedback-manager` integration needs configuration, a custom
  store/router/handler/policy, debugging, or tests.

Do not select it merely because an application needs a generic event bus,
application-wide logging, a database ORM, or an evaluator/scoring framework.

## Required workflow

### Before changing an application

1. Inspect the installed/current `feedback-manager` version and the existing
   `FeedbackManager(...)` construction. In this repository,
   [`pyproject.toml`](https://github.com/smuniharish/feedback-manager/blob/master/pyproject.toml)
   and
   [`src/feedback_manager/__init__.py`](https://github.com/smuniharish/feedback-manager/blob/master/src/feedback_manager/__init__.py)
   are the version and public-API sources.
2. Verify the project's `langchain-core`, `langgraph`, and `langgraph-xai`
   versions against its lockfile or dependency manifest.
   `feedback-manager` declares `langchain-core>=1.6,<2`, `langgraph>=1.2.11,<1.3`,
   and `langgraph-xai>=0.1.0,<0.2` (mandatory, not optional); do not infer
   compatibility for another installed release.
3. Search the application's existing store, router, handlers, policies, and
   observability sink. Preserve deliberate wiring; `FeedbackManager`'s
   dependencies are all optional constructor keyword arguments with
   in-memory/no-op defaults, so partial replacement is normal.
4. Start from the repository example that matches the workload; see
   [`references/integration.md`](references/integration.md) and
   [`examples/`](https://github.com/smuniharish/feedback-manager/tree/master/examples).
5. Use the supported constructor, documented methods, and documented contracts
   only. The package has no CLI, global registry, or hidden state; every
   `FeedbackManager` instance is fully independent.

### Choose the right response to the task

1. **Capture a human correction, approval, or rejection:** call
   `manager.submit(source=..., category=..., target=..., payload=...)` and
   drive it through the lifecycle with `acknowledge()` / `mark_handled()` /
   `resolve()`. See [`references/integration.md`](references/integration.md).
2. **Correlate feedback with a LangGraph run:** build an `ExecutionContext`
   from `execution_context_from_config()` (or construct one directly) and
   pass it as `execution_context=...` to `submit()`.
3. **Record a human-in-the-loop decision around a native interrupt:** use
   `HumanInTheLoopBridge` (`request()` -> `resolve()` -> `resume_command()`).
   Do not build a second interrupt/resume mechanism; LangGraph still owns
   pause, persistence, and resume.
4. **Capture tool/callback failures automatically:** attach
   `FeedbackCallbackHandler` through LangChain's normal `callbacks=[...]`
   configuration, or wrap non-callback tool code with
   `capture_tool_feedback()`.
5. **Need production persistence, routing, handling, or policy:** implement the
   matching contract in `feedback_manager.contracts` and inject it through
   `FeedbackManager(...)` keyword arguments. See
   [`references/extensibility.md`](references/extensibility.md). Do not
   subclass or monkeypatch `FeedbackManager` itself for this.
6. **Need provenance (which execution produced this?):** construct a
   `langgraph_xai.XAIRuntime` for the graph and pass it as
   `xai_runtime=...`; do not implement or select a different provenance
   adapter, since `langgraph-xai` is the sole, mandatory provenance source.
7. **A submission, transition, routing, or handler call misbehaves:**
   reproduce with the exact source/category/target/payload/context in use and
   follow [`references/troubleshooting.md`](references/troubleshooting.md)
   rather than adding speculative retries or silent `except` blocks.

## Integration rules

- Only construct `FeedbackManager` once per logical application/tenant
  boundary and reuse it; it holds subscribers and stream queues that should
  not be duplicated per request.
- Preserve the happy-path lifecycle
  `RECEIVED -> ACKNOWLEDGED -> HANDLED -> RESOLVED`; `resolve()` requires the
  event to already be `HANDLED`, and `ACKNOWLEDGED -> RESOLVED` is illegal.
  Use `reject()`, `cancel()`, or `expire()` for terminal alternatives instead
  of forcing an illegal transition.
- Treat `FeedbackSource`, `FeedbackCategory`, and `FeedbackTargetType` as open
  string enums: prefer the documented members, but custom string values are
  legitimate and do not require modifying the package.
- Do not swallow the exceptions in `feedback_manager.errors`; they all derive
  from `FeedbackManagerError` and carry `feedback_id`/context, so catch the
  specific exception you can handle and let the rest propagate.
- Store failures are blocking by default; routing, handler, subscriber, and
  provenance failures are best-effort/isolated by `FailurePolicy`. Do not
  "fix" a best-effort failure by making `submit()` itself swallow store
  errors.
- Idempotency is handled by `idempotency_key` at submission and by same-state
  lifecycle transitions being legal no-ops; do not add a second
  deduplication layer in application code without checking
  [`references/architecture.md`](references/architecture.md) first.

## Reference material map

| Need | File |
| --- | --- |
| Domain model, lifecycle, correlation, provenance ownership | [`references/architecture.md`](references/architecture.md) |
| Wiring into a LangChain/LangGraph application, quickstart | [`references/integration.md`](references/integration.md) |
| Custom stores, routers, handlers, policies, observability | [`references/extensibility.md`](references/extensibility.md) |
| Errors, failure isolation, concurrency, debugging steps | [`references/troubleshooting.md`](references/troubleshooting.md) |

## Prohibited shortcuts

Do **not**:

- manually truncate, drop, or fabricate feedback records instead of using
  `FeedbackManager`'s submission and lifecycle methods;
- build a second event bus, ticketing model, or feedback schema alongside
  `FeedbackEvent`;
- subclass or monkeypatch `FeedbackManager` to add a store, router, handler,
  policy, or observability integration instead of injecting one through its
  constructor;
- implement a custom provenance adapter; `langgraph-xai` is the sole,
  mandatory provenance source;
- silently reorder or drop an application's existing middleware, callback, or
  handler wiring;
- invent imports, CLI commands, environment variables, or constructor options
  not present in `feedback_manager.__init__` or the contracts;
- catch `FeedbackManagerError` (or a subclass) and discard it without
  handling the failure or re-raising;
- modify `src/feedback_manager/` while the task is only integration or skill
  content.

## Verification checklist

For an application change, add or update a focused test that uses a real
`FeedbackEvent`/`ExecutionContext` shape and asserts the relevant outcome:
lifecycle transition, correlation, routing selection, handler invocation,
failure isolation, or provenance resolution. Run the project's format, lint,
type, and test commands (`uv run ruff check`, `uv run mypy`,
`uv run pytest -q`).

For changes to this skill, follow
[`../../validation/README.md`](../../validation/README.md). Consult the
authoritative
[feedback-manager documentation](https://feedback-manager.readthedocs.io)
and
[example collection](https://github.com/smuniharish/feedback-manager/tree/master/examples)
rather than expanding this file into a second manual.
