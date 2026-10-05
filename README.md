# feedback-manager

[![PyPI](https://img.shields.io/pypi/v/feedback-manager.svg)](https://pypi.org/project/feedback-manager/)
[![Python](https://img.shields.io/pypi/pyversions/feedback-manager.svg)](https://pypi.org/project/feedback-manager/)
[![CI](https://github.com/smuniharish/feedback-manager/actions/workflows/ci.yml/badge.svg)](https://github.com/smuniharish/feedback-manager/actions/workflows/ci.yml)
[![Docs](https://readthedocs.org/projects/feedback-manager/badge/?version=latest)](https://feedback-manager.readthedocs.io/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/smuniharish/feedback-manager/blob/master/LICENSE)

**Feedback infrastructure for LangChain and LangGraph applications: capture,
correlate, store, route, and resolve feedback as a first-class concern.**

Agent applications constantly receive feedback: a reviewer approves a refund,
a user corrects an answer, a tool times out, an evaluator scores a response.
`feedback-manager` turns each of them into a validated, queryable record that
knows which run, node, tool call, or interrupt it is about, groups it with
related feedback, routes it to your handlers, and tracks it through an explicit
lifecycle until it is resolved. LangGraph and LangChain keep owning execution;
`feedback-manager` owns the feedback about it.

![feedback-manager architecture](https://raw.githubusercontent.com/smuniharish/feedback-manager/master/docs/assets/diagrams/architecture-overview.png)

## Features

- **One model for every kind of feedback.** Human corrections, approvals, tool
  failures, evaluator scores, and interruptions share one immutable event, with
  open sources, categories, and target types you can extend without
  subclassing.
- **Captured where it happens.** A LangChain callback handler records tool,
  model, retriever, and node failures once, with the thread, node, and tool call
  they happened in. A bridge records LangGraph human-in-the-loop requests and
  decisions around native `interrupt()` and `Command(resume=...)`.
- **An explicit, race-free lifecycle.** Feedback moves through a validated state
  machine. Transitions are compare-and-set operations on the store, so
  concurrent updates never overwrite each other and every change is published
  exactly once. Retried calls are idempotent.
- **Linked to provenance.** With a [langgraph-xai](https://github.com/smuniharish/langgraph-xai)
  runtime, feedback points at the run, node, tool call, decision, and evidence
  it is about.
- **Isolated from failures.** A failing handler, subscriber, router, or
  observability sink never loses stored feedback. Choose per stage whether a
  failure is logged or raised.
- **Bring your own infrastructure.** Swap the store, router, correlator,
  policies, and observability sink through small, typed contracts. Complete
  PostgreSQL and SQLite stores are included as examples.

## Install

```bash
pip install feedback-manager
```

Requires Python 3.12+. Installs `langgraph`, `langgraph-xai`, `pydantic`, and
`structlog`; `langchain-core` comes with `langgraph`.

## Quickstart

```python
import asyncio

from feedback_manager import (
    ExecutionContext,
    FeedbackManager,
    FeedbackQuery,
    FeedbackTarget,
    FeedbackTargetType,
)


async def main() -> None:
    manager = FeedbackManager()

    # A person corrects a generated answer in conversation "support-7".
    feedback = await manager.submit(
        source="human",
        category="correction",
        target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id="gen-42"),
        payload={"corrected_text": "Canberra is the capital of Australia."},
        execution_context=ExecutionContext(thread_id="support-7"),
    )
    print(f"{feedback.status}: correlated by {feedback.correlation_id}")

    # Take it through its lifecycle once the correction is applied.
    await manager.acknowledge(feedback.feedback_id)
    await manager.mark_handled(feedback.feedback_id)
    resolved = await manager.resolve(feedback.feedback_id, resolution={"applied_to": "faq"})
    print(f"{resolved.status}: {resolved.resolution}")

    # Query it back, like any other feedback.
    for event in await manager.query(FeedbackQuery(correlation_id="support-7")):
        print(event.source, event.category, event.target.id, event.status)


asyncio.run(main())
```

Output:

```text
received: correlated by support-7
resolved: {'applied_to': 'faq'}
human correction gen-42 resolved
```

Record every tool, model, and node failure of an agent, without changing your
error handling:

```python
from feedback_manager.integrations.langchain import FeedbackCallbackHandler

await graph.ainvoke(inputs, {"callbacks": [FeedbackCallbackHandler(manager)]})
```

## How it relates to LangGraph, LangSmith, and langgraph-xai

LangGraph runs your agents and pauses them for human input. LangSmith and other
tracing tools show what ran. [langgraph-xai](https://github.com/smuniharish/langgraph-xai)
records why it was decided. `feedback-manager` manages the feedback about all of
it: what people, evaluators, and failing components said, what it refers to,
who handles it, and how it was resolved.

## Documentation

Full documentation: **[feedback-manager.readthedocs.io](https://feedback-manager.readthedocs.io/)**

- [Quickstart](https://feedback-manager.readthedocs.io/en/latest/getting-started/quickstart/)
- [Concepts](https://feedback-manager.readthedocs.io/en/latest/concepts/)
- [How-to guides](https://feedback-manager.readthedocs.io/en/latest/how-to/)
- [Examples](https://feedback-manager.readthedocs.io/en/latest/examples/)
- [API reference](https://feedback-manager.readthedocs.io/en/latest/api/)

Changes are listed in the
[changelog](https://github.com/smuniharish/feedback-manager/blob/master/CHANGELOG.md).
An [Agent Skill](https://feedback-manager.readthedocs.io/en/latest/agent-skills/)
teaches coding agents such as Claude Code, Codex, Cursor, and GitHub Copilot to
integrate `feedback-manager` correctly.

## Contributing

Contributions are welcome. See
[CONTRIBUTING.md](https://github.com/smuniharish/feedback-manager/blob/master/CONTRIBUTING.md)
for the development setup and checks, and
[SECURITY.md](https://github.com/smuniharish/feedback-manager/blob/master/SECURITY.md)
to report a vulnerability.

## License

Apache License 2.0. See
[LICENSE](https://github.com/smuniharish/feedback-manager/blob/master/LICENSE).
