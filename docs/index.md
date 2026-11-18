---
hide:
  - navigation
  - toc
---

<div class="fm-hero" markdown>

# feedback-manager

<p class="fm-tagline">Feedback management and agent learning for AI agents.
Capture corrections, approvals, failures and scores with the execution they are
about, route and resolve them, and let your agents learn from reviewed
feedback. LangChain and LangGraph are built in as an extra; any other framework
plugs in through one ABC.</p>

[Get started](getting-started/index.md){ .md-button .md-button--primary }
[Browse examples](examples/index.md){ .md-button }
[API reference](api/index.md){ .md-button }

</div>

## Why feedback-manager

Agent applications constantly receive feedback: a reviewer approves a refund, a
user corrects an answer, a tool times out, an evaluator scores a response. Most
of it is lost in logs, and the agent repeats the same mistake on the next run.
feedback-manager makes feedback a first-class, queryable record, and turns
reviewed feedback into lessons the agent retrieves at runtime. It never trains
or fine-tunes a model.

<div class="grid cards" markdown>

-   :material-message-check-outline:{ .lg .middle } __One model for every kind of feedback__

    ---

    Human corrections, approvals, tool failures, evaluator scores and
    interruptions share one immutable `FeedbackEvent`, with open sources,
    categories and target types.

-   :material-state-machine:{ .lg .middle } __An explicit, race-free lifecycle__

    ---

    Feedback moves through a validated state machine with compare-and-set
    transitions. Concurrent updates never overwrite each other, and original
    feedback is never changed.

-   :material-school-outline:{ .lg .middle } __Agents that learn__

    ---

    A background learner turns validated feedback into versioned lessons. A
    person reviews each one before any agent sees it, and it survives
    restarts with durable stores.

-   :material-magnify:{ .lg .middle } __RetrievalLoom-backed discovery__

    ---

    The default learning engine uses RetrievalLoom's offline indexes and
    revalidates every hit against the current scoped artifact.

-   :material-graph-outline:{ .lg .middle } __Linked to provenance__

    ---

    With an [agxai](https://pypi.org/project/agxai/) runtime, feedback points
    at the run, node, tool call, decision and evidence it is about, in your
    own agxai store.

-   :material-puzzle-outline:{ .lg .middle } __Framework-agnostic__

    ---

    LangChain and LangGraph through the `[langchain]` extra; Agno, CrewAI or
    your own loop through `FrameworkAdapter`; any database through
    `FeedbackStore`, certified with the bundled contract kits.

</div>

## Thirty-second tour

=== "pip"

    ```bash
    pip install feedback-manager
    ```

=== "uv"

    ```bash
    uv add feedback-manager
    ```

The following checkout example records two synthetic corrections, simulates
independent assessment and acceptance, then separately reviews and activates
the resulting candidate:

```python
--8<-- "examples/13_basic_learning.py"
```

Output:

```text
Candidate: candidate, evidence=2
Retrieved: Improvement: Search, validate results, then execute.
Search, validate results, then execute.
```

Everything runs in memory here. For durable stores and the separate assessment
enqueue step, see [background learning and recovery](how-to/background-learning.md):

![How feedback-manager fits into an application](assets/diagrams/architecture-overview.png){ .diagram }

## Let your coding agent integrate it

Install the [feedback-manager Agent Skills](agent-skills/index.md) and your
coding agent learns the workflow and rules for adding feedback capture and
learning to your application, and for extending feedback-manager to other
frameworks and databases:

```text title="Agent instruction"
Install the Agent Skills from https://github.com/smuniharish/feedback-manager
(feedback-manager-skills/skills/feedback-manager,
feedback-manager-skills/skills/feedback-manager-learning and
feedback-manager-skills/skills/feedback-manager-extend) into this
project, run the feedback-manager skill's scripts/verify_setup.py, then
add feedback capture to this application with feedback-manager,
following the skill's workflow and rules.
```

## Where to go next

<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } __[Quickstart](getting-started/quickstart.md)__

    ---

    Record, route and resolve your first feedback step by step.

-   :material-lightbulb-outline:{ .lg .middle } __[Concepts](concepts/index.md)__

    ---

    The feedback model, the lifecycle, and the feedback-to-learning loop.

-   :material-book-open-variant:{ .lg .middle } __[How-to guides](how-to/index.md)__

    ---

    LangChain failures, human-in-the-loop, learning, retrieval and custom
    components.

-   :material-code-braces:{ .lg .middle } __[Examples](examples/index.md)__

    ---

    Complete programs, tested in CI, from one correction to a Grafana
    dashboard.

-   :material-speedometer:{ .lg .middle } __[Benchmarks](benchmarks.md)__

    ---

    Measured submission and retrieval overhead, and how to reproduce it.

-   :material-api:{ .lg .middle } __[API reference](api/index.md)__

    ---

    Every public class and method, generated from the source.

</div>