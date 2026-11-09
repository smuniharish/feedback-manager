# Validating the feedback-manager skills

## Automated checks

Run from the repository root:

```bash
uv run pytest tests/skill
uvx --from skills-ref agentskills validate feedback-manager-skills/skills/feedback-manager
uvx --from skills-ref agentskills validate feedback-manager-skills/skills/feedback-manager-learning
uvx --from skills-ref agentskills validate feedback-manager-skills/skills/feedback-manager-extend
```

`tests/skill` runs offline in continuous integration. For every skill, it checks that:

- the skill passes the reference validator, `skills-ref`;
- `name` matches the directory, `license` is `Apache-2.0`, `metadata.version`
  equals the package version, and the description says when to use the skill
  and when not to;
- `SKILL.md` has fewer than 500 lines and links every file in `references/`,
  which is one level deep;
- every relative link resolves, with the exact case of the file name;
- `SKILL.md` mentions every bundled script and asset;
- `evals/evals.json` has at least three evaluations whose fixtures exist;
- `evals/trigger_queries.json` has at least 16 unique queries, balanced
  between train and validation splits and between queries that should and
  should not trigger, and no query triggers two skills.

It also runs the bundled code:

- `verify_setup.py` and `verify_learning.py` pass with warnings treated as
  errors, in text and `--json` modes, and the version ranges in
  `verify_setup.py` equal those in `pyproject.toml`;
- each skill's `assets/` tests pass;
- `check_component.py` exits 0 for the store template, 1 for a store that
  breaks a promise, and 2 for a factory it cannot load;
- the eval fixtures run.

## Source review

Automated checks cannot tell whether guidance is correct. For every change,
check each snippet and claim against its source:

| Claim area | Source of truth |
| --- | --- |
| Public imports, signatures, and the package version | [`src/feedback_manager/__init__.py`](../../src/feedback_manager/__init__.py), [`pyproject.toml`](../../pyproject.toml), and the [API reference](../../docs/api/index.md) |
| Submission, lifecycle, queries, and delivery | [`src/feedback_manager/api/manager.py`](../../src/feedback_manager/api/manager.py) and [`src/feedback_manager/core/lifecycle.py`](../../src/feedback_manager/core/lifecycle.py) |
| Component contracts | [`src/feedback_manager/contracts/`](../../src/feedback_manager/contracts/) and the contract kits in [`src/feedback_manager/testing.py`](../../src/feedback_manager/testing.py) |
| Learning, validation, and retrieval | [`src/feedback_manager/learning/`](../../src/feedback_manager/learning/), [`src/feedback_manager/retrieval/`](../../src/feedback_manager/retrieval/), and [`src/feedback_manager/storage/`](../../src/feedback_manager/storage/) |
| Framework adapters and failure capture | [`src/feedback_manager/integrations/`](../../src/feedback_manager/integrations/), [LangChain failures](../../docs/how-to/langchain-failures.md), and [human-in-the-loop](../../docs/how-to/langgraph-hitl.md) |
| Provenance and agxai components | [`src/feedback_manager/integrations/provenance/agxai.py`](../../src/feedback_manager/integrations/provenance/agxai.py) and [provenance](../../docs/concepts/provenance.md) |
| Failure isolation and security | [failure isolation](../../docs/how-to/failure-isolation.md) and [security](../../docs/operations/security.md) |
| Working patterns | [`examples/`](../../examples/) and [`tests/`](../../tests/) |

If a behavior has no implementation, test, or documentation, leave it out of
the skill rather than infer it.

## Activation review

`evals/trigger_queries.json` in each skill lists queries that should and should
not activate it. Review new queries against the description, and route
near-misses to the sibling skill:

| Task | Skill |
| --- | --- |
| "Let users rate and correct the agent's answers." | `feedback-manager` |
| "Record every tool timeout in our agent." | `feedback-manager` |
| "Which decision was this complaint about?" | `feedback-manager` |
| "Make the agent stop repeating corrected mistakes." | `feedback-manager-learning` |
| "Retrieval returns nothing for our agent." | `feedback-manager-learning` |
| "Store feedback in PostgreSQL." | `feedback-manager-extend` |
| "Support CrewAI agents." | `feedback-manager-extend` |
| "Build a dashboard UI for traces." | none |