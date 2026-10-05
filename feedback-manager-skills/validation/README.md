# Validating the feedback-manager skill

## Automated checks

Run from the repository root:

```bash
uv run pytest tests/skill
uvx --from skills-ref agentskills validate feedback-manager-skills/skills/feedback-manager
```

`tests/skill` runs offline in continuous integration. It checks that:

- the skill passes the reference validator, `skills-ref`, which applies the
  specification's rules for `name`, `description`, `compatibility`, and the
  other frontmatter fields;
- `name` matches the directory, `license` is `Apache-2.0`, and
  `metadata.version` equals the package version;
- `SKILL.md` has fewer than 500 lines, and `references/` is one level deep;
- every relative link in the skill resolves;
- `scripts/verify_setup.py` passes, with warnings treated as errors;
- `assets/test_feedback_integration.py` passes.

## Source review

Automated checks cannot tell whether guidance is correct. For every change,
check each snippet and claim against its source:

| Claim area | Source of truth |
| --- | --- |
| Public imports, signatures, and the package version | [`src/feedback_manager/__init__.py`](../../src/feedback_manager/__init__.py), [`pyproject.toml`](../../pyproject.toml), and the [API reference](../../docs/api/index.md) |
| Submission, lifecycle, queries, and delivery | [`src/feedback_manager/api/manager.py`](../../src/feedback_manager/api/manager.py) and [`src/feedback_manager/core/lifecycle.py`](../../src/feedback_manager/core/lifecycle.py) |
| The store contract | [`src/feedback_manager/contracts/store.py`](../../src/feedback_manager/contracts/store.py) and [`tests/examples/test_stores.py`](../../tests/examples/test_stores.py) |
| Failure capture and human-in-the-loop | [`src/feedback_manager/integrations/`](../../src/feedback_manager/integrations/), [LangChain failures](../../docs/how-to/langchain-failures.md), and [human-in-the-loop](../../docs/how-to/langgraph-hitl.md) |
| Provenance | [`src/feedback_manager/integrations/xai/adapter.py`](../../src/feedback_manager/integrations/xai/adapter.py) and [provenance](../../docs/concepts/provenance.md) |
| Failure isolation and security | [failure isolation](../../docs/how-to/failure-isolation.md) and [security](../../docs/operations/security.md) |
| Working patterns | [`examples/`](../../examples/) and [`tests/`](../../tests/) |

If a behavior has no implementation, test, or documentation, leave it out of
the skill rather than infer it.

## Activation matrix

Review that the description and instructions lead an agent to the right
route for each task:

| Task | Activates | Route | Avoids |
| --- | --- | --- | --- |
| "Let users rate and correct the agent's answers." | Yes | Core workflow: `submit` with a target, execution context, and idempotency key; lifecycle methods as the feedback is worked on. | Mutating events or writing statuses into the store. |
| "Record every tool timeout in our agent." | Yes | `FeedbackCallbackHandler` in the run's callbacks; `capture_tool_feedback` for direct tool calls. | Wrapping every tool in try/except to submit feedback by hand. |
| "Keep an audit trail of human approvals." | Yes | LangGraph `interrupt()`, `HumanInTheLoopBridge.request` after the pause, `resolve`, and `resume_command`. | Calling `request` inside the node, or replacing LangGraph's interrupt. |
| "Store feedback in PostgreSQL." | Yes | A `FeedbackStore` with compare-and-set transitions, from the store examples, checked with the store contract tests. | Skipping `expected` in `transition`, or credentials in code. |
| "Which decision was this complaint about?" | Yes | One `XAIRuntime` shared by the graph and the manager; feedback during the run for decision IDs. | Copying provenance into payloads. |
| "Feedback has no provenance" or "the callback records nothing." | Yes | Troubleshooting. | Disabling validation or failure policies to hide errors. |
| "Build a dashboard UI for traces." | No | Not this package's purpose. | Inventing capabilities. |
