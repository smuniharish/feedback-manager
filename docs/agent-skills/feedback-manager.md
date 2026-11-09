# The feedback-manager skill

The `feedback-manager` skill teaches a coding agent to add feedback-manager to
an application built on LangChain, LangGraph or plain Python agents. Each piece
of feedback becomes a validated, immutable `FeedbackEvent` that is correlated
with its execution, stored, routed to handlers, and moved through an explicit
lifecycle until it is resolved.

[:material-github: View on GitHub](https://github.com/smuniharish/feedback-manager/tree/master/feedback-manager-skills/skills/feedback-manager){ .md-button }
[:material-download: Install](index.md#agentic-installation){ .md-button }

## When the agent uses it

The agent loads the skill to capture, store, route, query or resolve feedback:
human corrections, ratings, approvals, evaluator scores, tool or model failures,
and interruptions. It also loads it when feedback is missing, duplicated,
rejected with a validation or lifecycle error, or has no provenance.

To make agents learn from that feedback, it hands over to the
[feedback-manager-learning skill](feedback-manager-learning.md). To write a
store, adapter or other component, it hands over to the
[feedback-manager-extend skill](feedback-manager-extend.md).

## Workflows

=== "LangChain and LangGraph"

    1. Install `feedback-manager[langchain]` and create one `FeedbackManager`
       per application or tenant boundary, with a durable `store` in
       production.
    2. Submit feedback where it originates, with
       `execution_context_from_config(config)` and an `idempotency_key`.
    3. Add `FeedbackCallbackHandler(manager)` to the run's callbacks so tool
       and model failures are recorded once and still propagate.
    4. Record human-in-the-loop requests and decisions around each interrupt.
    5. Test with a strict manager, starting from
       `assets/test_feedback_integration.py`.

=== "Custom agents"

    1. Install `feedback-manager`; no framework extra is needed.
    2. Build an `ExecutionContext` from your agent's own run and step
       identifiers, or extract it with `CustomAgentAdapter`.
    3. Wrap each tool call in `capture_tool_feedback`, so each failure is
       stored once and the exception still reaches the agent.
    4. Route feedback to handlers and move it through the lifecycle:
       `acknowledge`, `mark_handled`, then `resolve`.

Before finishing, the agent checks that:

- feedback capture observes the agent and never changes how it runs;
- retries and repeated clicks reuse one `idempotency_key`;
- each piece of feedback carries the execution it is about;
- the project's tests and `scripts/verify_setup.py` pass.

## What's inside

```text
feedback-manager/
??? SKILL.md                          # workflow, rules, where things go
??? references/
?   ??? api.md                        # public imports, methods, models, errors
?   ??? recipes.md                    # complete patterns for each integration
?   ??? troubleshooting.md            # symptoms, causes, and fixes
??? scripts/
?   ??? verify_setup.py               # offline setup and behaviour check
??? assets/
?   ??? test_feedback_integration.py  # a pytest template for your integration
??? evals/                            # test prompts, assertions, trigger queries
```

## The setup check

`scripts/verify_setup.py` checks Python and the package versions, then runs the
feedback, lifecycle and failure-capture flows offline. With the `[langchain]`
extra installed, it also runs the LangChain failure capture, LangGraph
human-in-the-loop and agxai provenance flows. [Install in your
agent](install.md#verify) shows its output.

| Option | Effect |
| --- | --- |
| `--langchain` | Fail instead of skipping when the `[langchain]` extra is missing |
| `--json` | Print one JSON object for the agent to parse |

It exits with `0` when every check passes and `1` otherwise.

## Learn more

- [Quickstart](../getting-started/quickstart.md): the first feedback event.
- [Record LangChain failures](../how-to/langchain-failures.md) and
  [human-in-the-loop decisions](../how-to/langgraph-hitl.md).
- [Link feedback to provenance](../how-to/xai-provenance.md): agxai decisions
  and evidence.
- [Test your integration](../how-to/testing.md).
