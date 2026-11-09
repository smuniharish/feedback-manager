# feedback-manager Agent Skills

This directory distributes three [Agent Skills](https://agentskills.io) that
teach coding agents to use and extend the `feedback-manager` package. They are
not part of the Python package and add no runtime behavior.

| Skill | Use it to |
| --- | --- |
| [`feedback-manager`](skills/feedback-manager/SKILL.md) | Capture, store, route, query, and resolve feedback; record human-in-the-loop decisions; link feedback to agxai provenance |
| [`feedback-manager-learning`](skills/feedback-manager-learning/SKILL.md) | Turn validated feedback into reviewed lessons, run the background learner, and retrieve lessons into agents |
| [`feedback-manager-extend`](skills/feedback-manager-extend/SKILL.md) | Write and certify your own stores, framework adapters, repositories, vector and graph stores, embedding providers, and other components |

## Install

```bash
npx skills add smuniharish/feedback-manager                                     # all three
npx skills add smuniharish/feedback-manager --skill feedback-manager-learning   # one
```

The [`skills.sh.json`](../skills.sh.json) file at the repository root points
the `skills` CLI at this directory. Other installation options are in the
[documentation](https://feedback-manager.readthedocs.io/en/latest/agent-skills/).

## Layout

Each skill directory has the same shape:

| Path | Contents |
| --- | --- |
| `SKILL.md` | When to use the skill, its workflow, and the rules an integration follows |
| `references/` | Detail an agent loads only when a task needs it, one level deep |
| `scripts/` | Offline checks: `verify_setup.py`, `verify_learning.py`, and `check_component.py` |
| `assets/` | pytest templates to copy into a project |
| `evals/` | `evals.json` task evaluations, `trigger_queries.json` activation queries, and the fixtures they use |

[`validation/`](validation/) describes how the skills are validated and reviewed.

## Format

The skills follow the [Agent Skills specification](https://agentskills.io/specification):

- The `SKILL.md` frontmatter has `name`, which matches the directory, and
  `description`, `license`, `compatibility`, and `metadata.version`. The
  version equals the `feedback-manager` release the skill describes.
- Each description starts with "Use this skill when" and says when not to use
  it, naming the sibling skill that applies instead.
- `SKILL.md` stays under 500 lines. Scripts and assets are referenced by paths
  relative to the skill directory.

Every Agent Skills host reads the same directories, so there is no separate
copy for Claude Code, Codex, Cursor, or GitHub Copilot.

## Maintaining the skills

When the public API or documented behavior changes:

1. Update the affected skill, and `metadata.version` with each release.
2. Check every changed snippet and claim against the code, tests, or
   documentation. Leave out anything that is not implemented.
3. Run the checks in [`validation/README.md`](validation/README.md).

The skills are covered by the repository's Apache License 2.0; see
[LICENSE](../LICENSE).