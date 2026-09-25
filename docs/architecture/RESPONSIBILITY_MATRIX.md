# Responsibility Matrix

This matrix documents what `feedback-manager` owns and what it intentionally leaves to LangChain, LangGraph, `langgraph-xai`, or application code.

## What FeedbackManager owns

| Concern | Owned here? | Notes |
| --- | --- | --- |
| Typed feedback event model | Yes | `core/events.py`, `core/sources.py`, `core/categories.py`, `core/targets.py` |
| Feedback lifecycle state machine | Yes | `core/lifecycle.py` and lifecycle methods on `FeedbackManager` |
| Correlation identifiers for feedback | Yes | `CorrelationContext`, `ExecutionContext`, `DefaultFeedbackCorrelator` |
| Persistence contract for feedback | Yes | `FeedbackStore` ABC plus `InMemoryFeedbackStore` reference implementation |
| Routing contract and default rule router | Yes | `FeedbackRouter`, `RoutingRule`, `DefaultFeedbackRouter` |
| Handler contract | Yes | `FeedbackHandler` ABC |
| Subscriber and stream delivery for feedback events | Yes | `subscribe()` and `stream()` on `FeedbackManager` |
| Failure isolation around feedback stages | Yes | `FailurePolicy`, `FailureMode`, `FeedbackStage` |
| Provenance adapter | Yes | `XAIProvenanceAdapter` (langgraph-xai is the mandatory, sole provenance source) |
| Feedback observability events | Yes | `ObservabilityEvent`, `ObservabilitySink` |

## What FeedbackManager does not own

| Concern | Owned by | Why it stays out of scope |
| --- | --- | --- |
| Agent runtime | LangChain / LangGraph / app code | This package records feedback about execution; it does not execute agents |
| Graph orchestration | LangGraph | Graph construction, node scheduling, checkpointing, and resumptions remain native |
| Interrupt engine | LangGraph | `HumanInTheLoopBridge` wraps `interrupt()`; it does not replace it |
| Streaming engine | LangGraph / LangChain | `extract_interrupts()` only recognizes stream payloads already emitted by LangGraph |
| Tool execution | LangChain / app code | The package can observe tool failures, not invoke tools |
| LLM generation | LangChain / model providers | Generation feedback is captured around model calls, not produced by this library |
| Evaluators or judges | app code | Evaluator outputs can be recorded, but evaluator logic is not implemented here |
| Self-improvement loops | app code | No tuning, policy learning, or automatic behavior mutation exists here |
| Business decisions | app code | A low score, correction, or approval request is recorded and routed; the business response is yours |
| Access control | app code / custom store | No built-in authz system exists |
| Retention scheduler | app code | `RetentionPolicy` is a predicate only; no background sweeper is bundled |
| External notifications | app code / custom handlers | No Slack/email/ticketing adapters ship with the package |
| MCP routing or transport | app code / MCP systems | Tools like `capture_tool_feedback()` observe failures but do not route MCP traffic |
| Custom checkpoint stores | LangGraph / app code | This package does not wrap or replace checkpoint persistence |
| Provenance capture engine | `langgraph-xai` | Only translation into `FeedbackProvenanceReference` is implemented |

## Integration boundary principle

The code follows a simple rule:

> reuse the owning framework's mechanism first, then add a thin adapter only where feedback needs to cross that boundary.

Examples:

- LangChain callbacks are reused directly through `FeedbackCallbackHandler`.
- LangGraph interrupt/resume is reused directly through `HumanInTheLoopBridge`.
- `langgraph-xai` runtime and provenance store are reused directly through `XAIProvenanceAdapter`.

## Consequence for application design

Applications should treat `FeedbackManager` as one subsystem in a larger stack:

- use LangGraph for runtime orchestration
- use LangChain for model/tool composition
- use `langgraph-xai` for provenance
- use `FeedbackManager` to persist and route feedback about those runs

