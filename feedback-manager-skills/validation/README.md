# feedback-manager Agent Skill validation

This directory documents the repeatable validation process for the canonical
skill. It is intentionally not a second runtime test suite and does not
provide a host-specific package.

The Agent Skills specification was checked at
[agentskills.io/specification](https://agentskills.io/specification). It
defines `name` and `description` as the required frontmatter. No official
validator is specified there, so validation combines structural checks with
source-backed content review.

## Structural validation

For every change:

1. Confirm [`../skills/feedback-manager/SKILL.md`](../skills/feedback-manager/SKILL.md)
   exists and starts with YAML frontmatter.
2. Confirm `name` is exactly `feedback-manager` (the directory name),
   contains only lowercase letters and hyphens, and is at most 64 characters.
3. Confirm `description` is non-empty, at most 1024 characters, and states
   both the capability and when to activate it.
4. Confirm only `name` and `description` appear in frontmatter unless the
   current specification and a demonstrated host requirement justify more.
5. Resolve every relative Markdown target in the skill, its references, and
   this directory; no target may point at a deleted file.
6. Confirm the distribution contains one `skills/feedback-manager/` canonical
   knowledge source and no Claude/Codex/Copilot duplicate.
7. Search the distribution for invented CLI commands, stale package names,
   credentials, and unrelated projects.

## Source-accuracy review

Review every code snippet and factual claim against its source:

| Claim area | Source of truth |
| --- | --- |
| Public import and package version | [`src/feedback_manager/__init__.py`](../../src/feedback_manager/__init__.py) |
| `FeedbackManager` constructor signature and pipeline | [`src/feedback_manager/api/manager.py`](../../src/feedback_manager/api/manager.py) |
| Dependencies and supported Python/LangChain/LangGraph floors | [`pyproject.toml`](../../pyproject.toml) |
| Contracts (store, router, handler, correlator, policy) | [`src/feedback_manager/contracts/`](../../src/feedback_manager/contracts) |
| Errors | [`src/feedback_manager/errors/exceptions.py`](../../src/feedback_manager/errors/exceptions.py) |
| Observability events | [`src/feedback_manager/observability/hooks.py`](../../src/feedback_manager/observability/hooks.py) |
| Architecture and domain rules | [`docs/architecture/`](../../docs/architecture) |
| Concepts (lifecycle, routing, stores, handlers, provenance, subscriptions) | [`docs/concepts/`](../../docs/concepts) |
| Framework integration | [`docs/frameworks/langchain.md`](../../docs/frameworks/langchain.md), [`docs/frameworks/langgraph.md`](../../docs/frameworks/langgraph.md) |
| Executable workflows | [`examples/`](../../examples) and [`tests/`](../../tests) |

If a behavior lacks an implementation, test, or authoritative document, omit
it from the skill rather than infer an API.

## Agent-task matrix

The following matrix was reviewed against the canonical
[`SKILL.md`](../skills/feedback-manager/SKILL.md), its references, the
runtime implementation, and the linked examples/tests.

| Task | Activates | Grounded route | Avoids |
| --- | --- | --- | --- |
| "Record human corrections to generated answers." | Yes | `references/integration.md` → quickstart and `examples/01_human_correction.py`. | Ad hoc logging or a duplicate feedback model. |
| "Capture the human decision around my LangGraph interrupt approval gate." | Yes | `HumanInTheLoopBridge` flow in `references/integration.md`. | A second interrupt/resume mechanism. |
| "Turn tool timeouts into queryable records." | Yes | `FeedbackCallbackHandler` / `capture_tool_feedback()` guidance. | Silently swallowing tool exceptions. |
| "Why did resolve() raise a lifecycle error?" | Yes | `references/troubleshooting.md` lifecycle section and `docs/architecture/LIFECYCLE.md`. | Forcing an illegal transition or catching `Exception` broadly. |
| "I need Postgres persistence for feedback." | Yes | `references/extensibility.md` store contract plus `examples/postgres_feedback_store.py`. | Subclassing `FeedbackManager` or skipping `validate_transition()`. |
| "Add provenance so I know which run produced this feedback." | Yes | `xai_runtime=...` wiring in `references/integration.md` and `references/architecture.md`. | Implementing a custom provenance adapter. |
| "My custom handler's failure isn't propagating — is that a bug?" | Yes | Failure-isolation explanation in `references/troubleshooting.md`. | Assuming a package defect without checking `FailurePolicy`. |
| "Wire feedback events into our Grafana/OpenTelemetry setup." | Yes | `ObservabilitySink` guidance in `references/extensibility.md` and `examples/11_grafana_dashboard.py`. | Adding tracing calls inside handlers instead of a sink. |

## Repository validation

Skill-only work should at least run the structural/link/source review above
and review the resulting Git diff. If runtime files change, run the
project's existing checks (for example `uv run ruff check`,
`uv run mypy`, and `uv run pytest -q`) before treating the change as
complete.
