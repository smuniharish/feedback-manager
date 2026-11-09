# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.2] - 2026-11-06

feedback-manager collects, validates, stores, and routes feedback about AI
agents, and learns from validated feedback so agents improve their future
behaviour. It is framework-agnostic: LangChain and LangGraph are one optional
adapter, and any other agent framework plugs in through the same base classes.
This release describes the complete public API.

### Feedback management

- `FeedbackManager` submits feedback idempotently per tenant, moves it through
  a compare-and-set lifecycle (`acknowledge`, `mark_handled`, `resolve`,
  `reject`, `cancel`, `expire`, `expire_overdue`), queries it with
  `FeedbackQuery`, and delivers every change to subscribers and streams. It is
  an async context manager.
- `FeedbackEvent` records the source, category, target, payload, execution
  context (`tenant_id`, `agent_id`, `run_id`, `thread_id`, `node_id`,
  `tool_call_id`, `interrupt_id`, and more), correlation ID, provenance, and
  resolution of each piece of
  feedback. Original feedback is never rewritten by learning.
- Correlation, routing to handlers, lifecycle authorization, redaction,
  per-stage failure isolation, and observability events are each one
  replaceable component.

### Agent learning

- `LearningEngine` extracts learning signals from validated feedback,
  discovers repeated errors and corrections, aggregates evidence, estimates
  confidence, detects conflicts, and publishes versioned candidate artifacts
  that a reviewer validates and activates.
- `BackgroundLearner` learns automatically from durable, scoped jobs with
  lease fencing, bounded retries, dead letters, and replay checkpoints, and
  recovers after a restart. `FeedbackChangeSource` provides an ordered change
  feed committed with feedback writes.
- `ArtifactIndexer` projects artifacts into vector and graph stores through a
  durable outbox. `StructuredRetriever`, `VectorRetriever`, `GraphRetriever`,
  and `HybridRetriever` return only current, reviewed, in-scope artifacts.
- `LanceDBVectorStore` and `LadybugGraphStore` (`lancedb` and `ladybugdb`
  extras), typed embedding spaces, and `HashingEmbeddingProvider` and
  `CallableEmbeddingProvider`.

### Integrations

- `FrameworkAdapter`, `ExecutionContextExtractor`, `FailureClassifier`,
  `HumanInTheLoopBridge`, and `LearnedContextInjector` define a framework
  integration. `integrations.frameworks.custom` integrates a hand-written agent
  loop or multi-agent system with no dependencies;
  `integrations.frameworks.langchain` (`langchain` extra) records LangChain and
  LangGraph failures and human-in-the-loop decisions.
- `AgxaiProvenanceProvider` links feedback to the agxai run, decision, and
  evidence it is about. `create_agxai_runtime` accepts agxai's store,
  registry, plugins, observability, attribution, explanation, and policy
  components.
- OpenTelemetry spans and duration histograms for learning, jobs,
  projections, retrieval, databases, and embeddings (`otel` extra), without
  feedback payloads or unbounded attributes.

### Extensibility

- Every extension point is an abstract base class, checked when the manager is
  built.
- Callers inject their own agxai provenance store, LanceDB connection and
  connect options, and LadybugDB database and database options.
- `feedback_manager.testing` holds public contract kits for feedback stores,
  artifact repositories, vector stores, graph stores, learning job stores, and
  embedding providers.

### Packaging

- Requires Python 3.12 or later and is tested on Python 3.12 to 3.15 on Linux,
  macOS, and Windows. Core dependencies are `agxai`, `pydantic`, and
  `structlog`. The `lancedb` and `ladybugdb` extras install on Python 3.14
  or earlier.
- Releases include build provenance attestations and a CycloneDX SBOM, and
  CodeQL scans the code and workflows.
- Built with `uv_build`. Documentation is built with Zensical.
- Three Agent Skills in `feedback-manager-skills/`, plus container, Compose,
  and Kubernetes examples for the background learner.

## [0.1.1] - 2026-10-04

A correctness and hardening release.

### Added

- `FeedbackManager.expire_overdue(policy, *, now=None)`, and
  `RetentionPolicy.cutoff()`.
- `FeedbackQuery` filters by `created_after`, `created_before`, and
  `idempotency_key`, and returns newest events first with `newest_first`.
- `FeedbackEvent.correlation_id` and `FeedbackEvent.resolution`.
- `FeedbackStore.transition` is a compare-and-set with an `expected` status.
- `FeedbackStream`, registered when `stream()` returns and closable.
- `FeedbackConflictError`, `FeedbackSubscriberError`, and a
  `FeedbackManagerError` base for every error the package raises.
- `ExecutionContext.interrupt_id` and `execution_context_from_snapshot()`.
- `FeedbackManager` as an async context manager, with `aclose()`.
- `known_values()` on `FeedbackSource`, `FeedbackCategory`, and
  `FeedbackTargetType`; lifecycle helpers in `feedback_manager.core`.
- A durable SQLite store example and a Docker Compose file for the PostgreSQL
  and Grafana examples.

### Fixed

- Lifecycle calls validate a transition before writing its resolution, and a
  repeated call returns the event unchanged.
- Concurrent transitions cannot overwrite each other or publish a change twice.
- `InMemoryFeedbackStore` is safe across threads and event loops.
- Streams receive every event published after `stream()` returns.
- `feedback.failed` is emitted, and a failing observability sink never breaks
  processing.
- The LangChain callback handler records each failure once, about the right
  run, and keeps the run's context.
- `capture_tool_feedback` lets `KeyboardInterrupt`, `SystemExit`, and
  cancellation pass through and keeps the original exception.
- Identifiers, JSON payloads, and timestamps are validated.

## [0.1.0] - 2026-09-25

The first public release.

### Added

- The feedback domain model: `FeedbackEvent`, sources, categories, targets,
  execution context, provenance references, and the lifecycle state machine.
- `FeedbackManager`, with submission, lifecycle, queries, subscriptions, and
  streams.
- Extension contracts for stores, routers, handlers, correlators, subscribers,
  and policies, with an in-memory store, a rule-based router, a default
  correlator, failure isolation, and observability hooks.
- LangChain callbacks and tools, LangGraph human-in-the-loop interrupts, and
  provenance links.
- Examples, tests, and documentation.

[0.1.2]: https://github.com/smuniharish/feedback-manager/releases/tag/v0.1.2
[0.1.1]: https://github.com/smuniharish/feedback-manager/releases/tag/v0.1.1
[0.1.0]: https://pypi.org/project/feedback-manager/0.1.0/
