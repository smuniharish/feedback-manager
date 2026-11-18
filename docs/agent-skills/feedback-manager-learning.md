# The feedback-manager-learning skill

The `feedback-manager-learning` skill teaches a coding agent to make agents
improve from validated feedback. Learning happens at the agent level:
feedback becomes versioned, reviewed **learned artifacts** that agents retrieve
at runtime. No model is trained or fine-tuned, and original feedback is never
changed.

[:material-github: View on GitHub](https://github.com/smuniharish/feedback-manager/tree/master/feedback-manager-skills/skills/feedback-manager-learning){ .md-button }
[:material-download: Install](index.md#agentic-installation){ .md-button }

## When the agent uses it

The agent loads the skill to turn corrections and failures into learned
artifacts, run and recover the background learner, review and activate
artifacts, set learning scopes, retrieve learned context through RetrievalLoom,
and inject lessons into an agent. It also
loads it when no artifact is created, an artifact never becomes active,
retrieval returns nothing, or jobs are dead-lettered.

To capture or route feedback, it hands over to the
[feedback-manager skill](feedback-manager.md). To write a new store or adapter,
it hands over to the [feedback-manager-extend skill](feedback-manager-extend.md).

## Workflows

=== "Learn from corrections"

    1. Give the manager a `LearningEngine` on an `ArtifactRepository` you
       control.
    2. Submit feedback with a tenant and an agent, through `for_tenant` and
       the execution context.
    3. Independently assess the feedback, record a separate acceptance decision,
       and enqueue that assessment version. A feedback change feed does not
       recover missed assessment enqueues.
    4. Review: artifacts start as `CANDIDATE`; a person requests review,
       approves, and activates them.
    5. Retrieve and inject before the agent runs, with the framework
       adapter's `context_injector(engine)`.

=== "Durable learning"

    1. Use durable feedback, experience, assessment, job and artifact stores,
       and a feedback change source.
    2. Pass `require_durable_learning=True`, so an in-memory component is
       rejected at startup.
    3. Inspect dead-lettered jobs in the job store and requeue them with
       `replay_dead_letters()` once the cause is fixed.

=== "Retrieval customization"

    1. Use the default RetrievalLoom engine for offline keyword/fuzzy/sparse
       search; it does not download a model.
    2. When embeddings, graph traversal or durable indexes are required, inject
       RetrievalLoom's native components and resources.
    3. Treat indexes as projections and revalidate every hit against the
       authoritative artifact, assessment, review and host-authenticated scope.

Before finishing, the agent checks that:

- no artifact is activated without a person's review;
- the host, not a payload, proves the tenant, agent and reviewer;
- learned context stays in its own message, never in the instructions;
- the project's tests and `scripts/verify_learning.py` pass.

## What's inside

```text
feedback-manager-learning/
??? SKILL.md                          # workflow, rules, gotchas
??? references/
?   ??? learning.md                   # signals, evidence, validation, review
?   ??? retrieval.md                  # RetrievalLoom, scope, evidence, compatibility APIs
?   ??? operations.md                 # durability, retries, recovery
??? scripts/
?   ??? verify_learning.py            # offline end-to-end learning check
??? assets/
?   ??? test_learning_flow.py         # a pytest template for the learning flow
??? evals/                            # test prompts, assertions, trigger queries
```

## The learning check

`scripts/verify_learning.py` runs the whole lifecycle offline, from two
corrections to a reviewed lesson that reaches only the right agent and tenant:

```text
PASS  the background learner is enabled
PASS  the background learner processed both jobs
PASS  two corrections became one artifact
PASS  the artifact is a candidate with two pieces of evidence
PASS  a candidate is not retrieved
PASS  review activates the artifact
PASS  the agent receives the lesson
PASS  another agent receives nothing
PASS  another tenant receives nothing
All checks passed.
```

Add `--json` to print one JSON object. It exits with `0` when every check
passes and `1` otherwise.

## Learn more

- [Feedback-to-learning lifecycle](../concepts/learning.md): the model behind
  the skill.
- [Learn from scoped feedback](../how-to/basic-learning.md) and
  [automatic learning and recovery](../how-to/background-learning.md).
- [Retrieval with RetrievalLoom](../how-to/hybrid-retrieval.md).
