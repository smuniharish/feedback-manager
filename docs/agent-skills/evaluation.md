# Evaluation

A skill is only useful when an agent picks it up for the right task and then
gets that task right. feedback-manager tests both, following the Agent Skills
guidance on
[evaluating skills](https://agentskills.io/skill-creation/evaluating-skills).

## Checked on every change

`tests/skill` runs offline with the rest of the test suite in CI. For all three
skills, it checks:

- **Format.** The `skills-ref` reference validator accepts the frontmatter.
  `name` matches the directory, the description starts with "Use this skill
  when" and says when not to use it, and `metadata.version` equals the
  feedback-manager release.
- **Progressive disclosure.** `SKILL.md` stays under 500 lines and references
  are one level deep. Every reference is linked from `SKILL.md`, and every
  relative link resolves with its exact case.
- **Bundled files.** `SKILL.md` mentions every script and asset, so the agent
  can find it.
- **Scripts.**
    - `verify_setup.py` passes, prints valid JSON with `--json`, and
      requires the same dependency ranges as the package.
    - `verify_learning.py` passes and prints valid JSON with `--json`.
    - `check_component.py` passes a correct store and names the promises a
      broken store violates. It exits with a distinct code when the factory
      can't be loaded.
- **Templates.** The integration, learning-flow, store and adapter templates
  pass their own tests.
- **Discovery.** The skills CLI finds the three skills through
  `skills.sh.json`.

## Evaluated against real tasks

Each skill has an `evals/` folder:

| File | Contents |
| --- | --- |
| `evals.json` | Realistic tasks, each with input files, the expected result, and assertions a grader can verify |
| `trigger_queries.json` | 20 user requests: 10 that should load the skill and 10 near misses that shouldn't, split into training and validation sets |
| `files/` | The projects the tasks start from, such as a plain-Python agent without feedback-manager or an uncertified store |

The tasks cover what agents get wrong without the skill:

| Skill | Tasks |
| --- | --- |
| `feedback-manager` | Record a refund agent's gateway failures and supervisor decisions, with tests; stop duplicate ratings and fix `FeedbackLifecycleError`; link feedback to an existing agxai runtime |
| `feedback-manager-learning` | Make a support bot learn from reviewed corrections only; make learning durable and recover dead-lettered jobs; find why retrieval returns nothing |
| `feedback-manager-extend` | Certify and fix an in-house store; write an adapter for CrewAI; move learned artifacts to PostgreSQL for durable learning |

The near-miss trigger queries keep the three skills apart: many near misses for
one skill are tasks for another. A test checks that no query is meant to load
two skills.

To evaluate a change to a skill, run each task with and without the skill in a
fresh agent session. Grade the outputs against the assertions, and compare pass
rates, time, and tokens. The
[validation guide](https://github.com/smuniharish/feedback-manager/blob/master/feedback-manager-skills/validation/README.md)
describes the workflow.

## Reviewed against the source

Automated checks can't tell whether guidance is correct. Each snippet and claim
in a skill is reviewed against the code, tests and documentation it describes,
and anything without an implementation is left out. The
[validation guide](https://github.com/smuniharish/feedback-manager/blob/master/feedback-manager-skills/validation/README.md)
lists the source of truth for each area.
