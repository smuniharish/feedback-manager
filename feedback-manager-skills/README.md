# feedback-manager Agent Skills

This directory is the canonical Agent Skills distribution for
`feedback-manager`. It contains procedural guidance for coding agents that
need to integrate, configure, test, or debug the existing `feedback-manager`
package.

It is not a Python package and does not add runtime behavior.

| Component | Location | Purpose |
| --- | --- | --- |
| feedback-manager runtime | [`src/feedback_manager/`](https://github.com/smuniharish/feedback-manager/tree/master/src/feedback_manager) | The published Python package and its supported public API. |
| feedback-manager Agent Skill | [`skills/feedback-manager/`](skills/feedback-manager/) | Canonical agent-oriented instructions and concise reference material. |
| Skill validation | [`validation/`](validation/) | Validation procedure and realistic activation/task matrix. |

## Agent Skills format

The canonical skill follows the Agent Skills `SKILL.md` format: a
directory-scoped Markdown instruction file with required `name` and
`description` YAML frontmatter. Its `name` matches its containing directory
(`feedback-manager`), and only the specification's required frontmatter is
used for portability.

Compatible agents should load
[`skills/feedback-manager/SKILL.md`](skills/feedback-manager/SKILL.md) when
working on feedback capture, correlation, lifecycle, routing, persistence, or
provenance for LangChain/LangGraph applications. The skill links to the
repository's authoritative
[documentation](https://feedback-manager.readthedocs.io) and
[examples](https://github.com/smuniharish/feedback-manager/tree/master/examples)
instead of maintaining a second copy of them.

This repository intentionally provides no Claude, Codex, or Copilot adapter
because none is required to consume the canonical `SKILL.md`.

## Maintaining the distribution

When `feedback-manager`'s public API, supported integrations, or documented
behavior changes:

1. Update the canonical skill and only the reference material affected by
   that verified change.
2. Link to the corresponding implementation, tests, examples, or
   documentation; do not duplicate runtime logic.
3. Run the process in [`validation/README.md`](validation/README.md).
4. Do not add platform-specific copies of the skill text. Add thin metadata
   only when a host's current official documentation demonstrates it is
   required.

The distribution is covered by the repository's Apache-2.0 license; see the
[repository license](../LICENSE).
