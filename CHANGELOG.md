# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2026-10-04

A correctness and hardening release. It fixes defects in lifecycle handling,
concurrency, failure isolation, and the framework integrations, aligns the
package with `langgraph-xai` 1.0, and removes APIs that could not be used
correctly. Upgrading requires the changes listed under "Breaking changes".

### Breaking changes

#### Requirements

- Requires `langgraph-xai>=1.0.0,<2`, `langgraph>=1.2.12,<2`,
  `pydantic>=2.13.5,<3`, and `structlog>=26.1.0,<27`. `langchain-core` is no
  longer a direct dependency; it is installed with `langgraph`.

#### Domain model

- `FeedbackEvent.correlation` and `CorrelationContext` were replaced by
  `FeedbackEvent.correlation_id`, a string.
- Resolutions are stored in the new `FeedbackEvent.resolution` field instead of
  `metadata["resolution"]`.
- `FeedbackEvent.with_correlation` and `with_provenance` were removed;
  `with_status(status, *, resolution=None)` remains.
- Identifiers, sources, categories, and target types must be non-empty strings
  without leading or trailing whitespace.
- `payload`, `metadata`, `target.metadata`, and `resolution` must be JSON
  objects with finite numbers, and timestamps must be timezone-aware.

#### FeedbackManager

- `submit` stores feedback directly as `RECEIVED` in a single write, and no
  longer emits `feedback.created`.
- `list()` was removed; call `query()` without arguments.
- The `provenance_adapter` argument was removed; pass `xai_runtime`.
- `stream()` now returns a `FeedbackStream`, which is registered immediately
  and can be closed; it must be called from a running event loop. It receives
  changes made from any task, thread, or event loop, in the order they were
  published.
- `reject` and `cancel` accept a `resolution` next to `reason`.
- Lifecycle calls to the current status return the event unchanged and publish
  nothing.
- `REJECTED`, `CANCELLED`, and `EXPIRED` emit `feedback.rejected`,
  `feedback.cancelled`, and `feedback.expired`, instead of `feedback.resolved`.
- Invalid collaborators raise `FeedbackConfigurationError` instead of
  `ValueError`, and every error the package raises derives from
  `FeedbackManagerError`.

#### Contracts

- `FeedbackStore.transition` is a compare-and-set:
  `transition(feedback_id, status, *, expected, resolution=None)`.
  `FeedbackStore.update` and `list` were removed. `FeedbackStore.query` must
  support the new `FeedbackQuery` filters, which `FeedbackQuery.matches`
  implements.
- `FeedbackHandler.handle(feedback)` no longer receives a `FeedbackContext`,
  which was removed, and `FeedbackHandlerResult` no longer has `metadata`.
  `handle` must return a `FeedbackHandlerResult`, and `FeedbackRouter.route`
  must return `FeedbackHandler` instances; anything else fails the `HANDLER`
  or `ROUTING` stage.
- `FeedbackPolicy.apply` was renamed `FeedbackRedactionPolicy.redact`.
- `FeedbackSerializer` and `FeedbackSerializationError` were removed: events
  serialize with pydantic.
- `FeedbackStage.STORE` and `SERIALIZATION` were removed: store failures always
  raise `FeedbackStoreError`. `DeliveryMode` was removed.
- `FailurePolicy(modes=...)` overrides only the given stages and keeps the
  best-effort default for the others.

#### Integrations

- `FeedbackCallbackHandler(manager)` no longer takes a `config`: it reads each
  run's context from LangChain's callbacks. It records a cancelled run once,
  about the top-level run, and LangGraph node timeouts about the node.
- `HumanInTheLoopBridge.interrupt()` was removed; call LangGraph's
  `interrupt()` directly. `request()` takes the pending `interrupt` (or a
  `prompt`), and `resume_command()` accepts an `interrupt_id`.
- `capture_tool_feedback()` takes an optional `tool_name`, validates
  `tool_call_id` on entry, and lets a cancellation pass through without
  recording it.

### Added

- `FeedbackManager.expire_overdue(policy, *, now=None)` expires overdue pending
  feedback, and `RetentionPolicy` gained `cutoff()`.
- `FeedbackQuery` filters by `created_after`, `created_before`, and
  `idempotency_key`, returns newest events first with `newest_first`, and
  validates its fields.
- Each change of an event sets a later `updated_at` than the change before it,
  even when the clock has not advanced, so `updated_at` orders its changes.
- `FeedbackConflictError` and `FeedbackSubscriberError`.
- `ExecutionContext.interrupt_id`, and `execution_context_from_snapshot()` to
  build a context from a paused graph's state snapshot.
- Provenance references include the matching human interaction, and, for
  feedback submitted during a run, the latest decision and its evidence.
- `FeedbackManager` is an async context manager, and `aclose()` closes every
  open stream.
- `known_values()` on `FeedbackSource`, `FeedbackCategory`, and
  `FeedbackTargetType`.
- `Identifier`, `JsonObject`, `LEGAL_TRANSITIONS`, `TERMINAL_STATUSES`,
  `LifecycleTransition`, and `is_legal_transition` in `feedback_manager.core`.
- Examples: a durable SQLite store, and a Docker Compose file for the
  PostgreSQL and Grafana examples.

### Changed

- The documentation was reorganized into concepts, how-to guides, examples,
  API reference, and operations pages, with diagrams.
- The examples were rewritten for the new API. Example 08, now
  `08_agent_deepagents_hitl.py`, edits a temporary copy of its workspace
  instead of the repository's files.
- The Agent Skill in `feedback-manager-skills/` was rewritten for the new API.

### Fixed

- Illegal `resolve`, `reject`, or `cancel` calls wrote the resolution before
  the transition was validated, corrupting the event.
- Repeating a lifecycle call re-published the event, bumped `updated_at`, and
  overwrote the resolution.
- Concurrent transitions could overwrite each other and publish the same
  change twice, and the lifecycle policy authorized moves on stale state.
- `InMemoryFeedbackStore` was not safe across threads and event loops.
- Events published between `stream()` and its first iteration were lost.
- `FeedbackQuery(limit=-1)` silently dropped results.
- `feedback.failed` was never emitted, and a failing observability sink broke
  processing of feedback that was already stored.
- Overriding one failure mode reset the others.
- The handler's result was ignored, and `feedback.routed` did not report it.
- `FeedbackCallbackHandler` recorded one failure once per enclosing runnable
  and once per run a cancellation or a failing node stopped, recorded
  LangGraph interrupts as failures, and lost the run's context.
- An idempotent replay ran correlation and provenance resolution again before
  returning the stored event.
- `capture_tool_feedback` swallowed `KeyboardInterrupt` and `SystemExit`, and a
  recording failure replaced the original exception.
- `execution_context_from_config` rejected non-string IDs and ignored the run ID
  and node that `langgraph-xai` and LangGraph provide.
- `HumanInTheLoopBridge.resolve` failed for requests that were already
  acknowledged or handled.
- Post-run provenance looked up the wrong record, and interactions were not
  matched to their interrupt.
- `RetentionPolicy` treated handled feedback as expirable.
- Non-finite numbers were accepted but could not be serialized.
- Empty and padded identifiers were accepted.
- Disabled log levels were still rendered.
- `__version__` could drift from the installed version.
- Project URLs pointed to a repository that does not exist.

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
- Integrations for LangChain callbacks and tools, LangGraph human-in-the-loop
  interrupts, and `langgraph-xai` provenance.
- Examples, tests, and documentation.

[0.1.1]: https://github.com/smuniharish/feedback-manager/releases/tag/v0.1.1
[0.1.0]: https://pypi.org/project/feedback-manager/0.1.0/
