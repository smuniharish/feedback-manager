# Changelog

All notable changes to `feedback-manager` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-09-25

### Added

- Initial public release of `feedback-manager`.
- Core feedback domain model: `FeedbackEvent`, `FeedbackSource`,
  `FeedbackCategory`, `FeedbackTarget`/`FeedbackTargetType`,
  `ExecutionContext`, `CorrelationContext`, `FeedbackProvenanceReference`,
  and an explicit `FeedbackStatus` lifecycle state machine.
- Abstract extension contracts (`contracts/`): `FeedbackStore`,
  `FeedbackHandler`, `FeedbackRouter`, `FeedbackSubscriber`,
  `FeedbackCorrelator`, `FeedbackPolicy`, `FeedbackLifecyclePolicy`,
  `FeedbackSerializer`. Provenance is not a generic extension point:
  `langgraph-xai` is the mandatory, sole provenance source, and
  `FeedbackManager` depends directly on `XAIProvenanceAdapter`.
- `FeedbackManager` application service: `submit`, `acknowledge`,
  `mark_handled`, `resolve`, `reject`, `cancel`, `get`, `query`, `list`,
  `subscribe`, `stream` — a small, stable, dependency-injected public API.
- Reference implementations: `InMemoryFeedbackStore`, default router and
  rule-based routing, default correlator, failure-isolation policies
  (best-effort by default), observability hooks.
- Framework integrations as thin adapters:
  - LangChain: `FeedbackCallbackHandler` (tool/LLM/chain/retriever error
    capture), `capture_tool_feedback` context manager, error→category
    mapping.
  - LangGraph: `execution_context_from_config`, `HumanInTheLoopBridge`
    (wraps `interrupt()`/`Command(resume=...)`), interrupt-stream
    extraction helper.
  - `langgraph-xai` (mandatory runtime dependency): `XAIProvenanceAdapter`
    translating `Execution`/`NodeExecution`/`ToolExecution`/
    `HumanInteraction` into `FeedbackProvenanceReference`, live (in-run)
    and post-run (via `ProvenanceStore`). `FeedbackManager` accepts an
    `xai_runtime: XAIRuntime | None` and builds the adapter for you
    automatically.
- Structured logging via `structlog` (bundled `LoggingObservabilitySink`,
  `AuditFeedbackHandler`, and best-effort failure-stage warnings), wired
  through the stdlib `logging` module so host applications' existing
  logging configuration (handlers, filters, `caplog` in tests) continues
  to work without extra setup.
- Six runnable examples covering human correction, HITL approval/
  rejection, tool failure/timeout, generation lifecycle events, evaluator
  feedback, and `langgraph-xai` provenance correlation.
- Full test suite: unit, concurrency, and real-dependency integration
  tests (no framework mocking) — 100 tests, 97% coverage.
- Full documentation site (MkDocs + Material), architecture docs, and
  ADRs under `docs/architecture/`.

[Unreleased]: https://github.com/samamuniharish/feedback-manager/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/samamuniharish/feedback-manager/releases/tag/v0.1.0
