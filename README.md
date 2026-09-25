# feedback-manager

Production-grade feedback infrastructure for LangChain and LangGraph applications.

`feedback-manager` treats feedback as a first-class domain concern: capture it, correlate it to execution context, persist it, route it to handlers, and move it through an explicit lifecycle.

It is a **library**, not an agent framework or runtime.

## What it solves

Agent applications often need to handle feedback from many places:

- human corrections on generated answers
- approval or rejection decisions in human-in-the-loop flows
- tool failures and timeouts
- evaluator scores and critiques
- generation interruptions or partial results
- provenance-linked review or audit events

Without a dedicated feedback model, that data usually ends up fragmented across logs, UIs, tickets, and one-off tables.

`feedback-manager` gives you:

- a typed feedback event model
- correlation to runs, threads, checkpoints, nodes, tools, and generations
- explicit lifecycle management
- pluggable storage, routing, handlers, policies, and observability
- thin integrations for LangChain, LangGraph, and `langgraph-xai`

## What it does not do

`feedback-manager` does **not**:

- execute agents
- orchestrate graphs
- replace LangGraph interrupts, checkpoints, or streaming
- implement evaluators or LLM-as-judge systems
- perform self-improvement or policy learning
- own your application's business workflow

LangChain, LangGraph, `langgraph-xai`, and your application code keep those responsibilities.

## Installation

Requirements:

- Python `>=3.12,<3.15`

Install the package:

```powershell
pip install .
```

or for local development:

```powershell
uv sync --all-groups
```

Runtime dependencies are mandatory, not optional extras:

- `langchain-core>=1.6,<2`
- `langgraph>=1.2.11,<1.3`
- `langgraph-xai>=0.1.0,<0.2`
- `pydantic>=2.12,<3`

## Architecture at a glance

Layering:

1. **public API** — `FeedbackManager`, `FeedbackQuery`, core domain types
2. **application service** — orchestration in `api/manager.py`
3. **domain model** — events, lifecycle, contexts, provenance reference
4. **contracts** — store, router, handler, policies, subscribers
5. **infrastructure/integrations** — memory store, default router, adapters

The happy-path lifecycle is:

```text
RECEIVED -> ACKNOWLEDGED -> HANDLED -> RESOLVED
```

`resolve()` requires the event to already be `HANDLED`.

## Core concepts

### Source

Who or what produced the feedback:

- `human`
- `tool`
- `generation`
- `evaluator`
- `system`
- and custom open values

### Category

What kind of feedback it is:

- `correction`
- `approval`
- `rejection`
- `timeout`
- `quality`
- `interruption`
- and custom open values

### Target

What the feedback is about:

- graph
- run
- node
- tool call
- generation
- message
- state

### Correlation

Feedback can be linked to:

- `run_id`
- `thread_id`
- `checkpoint_id`
- `node_id`
- `tool_call_id`
- `generation_id`

### Provenance

When used with `langgraph-xai`, feedback can carry a `FeedbackProvenanceReference` resolved from an active run or from a provenance store by `run_id`.

## Quick start

```python
import asyncio

from feedback_manager import (
    FeedbackCategory,
    FeedbackManager,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.core.context import ExecutionContext


async def main() -> None:
    manager = FeedbackManager()

    feedback = await manager.submit(
        source=FeedbackSource.HUMAN,
        category=FeedbackCategory.CORRECTION,
        target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id="gen-42"),
        payload={
            "original_text": "The capital of Australia is Sydney.",
            "corrected_text": "The capital of Australia is Canberra.",
        },
        execution_context=ExecutionContext(generation_id="gen-42"),
    )

    await manager.acknowledge(feedback.feedback_id)
    await manager.mark_handled(feedback.feedback_id)
    resolved = await manager.resolve(
        feedback.feedback_id,
        resolution={"applied": True, "channel": "manual_review"},
    )

    print(resolved.status)
    print(resolved.metadata["resolution"])


asyncio.run(main())
```

## LangChain example

`FeedbackCallbackHandler` turns real LangChain callback errors into feedback:

```python
import asyncio

from langchain_core.tools import tool

from feedback_manager import FeedbackManager
from feedback_manager.integrations.langchain import FeedbackCallbackHandler


@tool
async def fetch_weather(city: str) -> str:
    raise TimeoutError(f"weather service timed out looking up {city!r}")


async def main() -> None:
    manager = FeedbackManager()
    handler = FeedbackCallbackHandler(manager)

    try:
        await fetch_weather.ainvoke({"city": "Canberra"}, config={"callbacks": [handler]})
    except TimeoutError:
        pass

    events = await manager.list()
    print(events[0].source, events[0].category, events[0].target.type)


asyncio.run(main())
```

## LangGraph example

Extract execution identifiers from a `RunnableConfig`:

```python
from feedback_manager.integrations.langgraph import execution_context_from_config

config = {
    "configurable": {"thread_id": "thread-1", "checkpoint_id": "cp-1"},
    "metadata": {"xai_application_id": "support-bot"},
}

context = execution_context_from_config(config, node_id="answer_node")
print(context.thread_id, context.checkpoint_id, context.node_id)
```

## HITL example

Use native LangGraph interrupts and record the approval request with `HumanInTheLoopBridge`:

```python
import asyncio

from feedback_manager import FeedbackManager, FeedbackTarget, FeedbackTargetType
from feedback_manager.integrations.langgraph import HumanInTheLoopBridge


async def main() -> None:
    manager = FeedbackManager()
    bridge = HumanInTheLoopBridge(manager)

    feedback = await bridge.request(
        target=FeedbackTarget(type=FeedbackTargetType.GRAPH, id="approval-flow"),
        prompt={"question": "Approve sending this email?"},
    )

    resolved = await bridge.resolve(feedback.feedback_id, response="approved", approved=True)
    resume = bridge.resume_command("approved")
    print(resolved.status, resume)


asyncio.run(main())
```

This complements LangGraph's runtime instead of replacing it.

## Provenance example

Attach provenance from `langgraph-xai` by passing the runtime directly --
`FeedbackManager` wires up the provenance adapter automatically:

```python
from langgraph_xai import XAIRuntime

from feedback_manager import FeedbackManager

runtime = XAIRuntime(
    application_id="support-bot",
    tenant_id="acme-corp",
    graph_id="qa-graph",
)
manager = FeedbackManager(xai_runtime=runtime)
```

When `manager.submit(...)` runs inside an instrumented graph node, the adapter can resolve provenance from `runtime.current_run`.

## Extension example

### Custom source/category values

```python
from feedback_manager import FeedbackCategory, FeedbackSource

source = FeedbackSource("mcp_server")
category = FeedbackCategory("business_policy_violation")
```

### Custom store

```python
from collections.abc import Sequence
from uuid import UUID

from feedback_manager.contracts import FeedbackQuery, FeedbackStore
from feedback_manager.core import FeedbackEvent, FeedbackStatus


class MyStore(FeedbackStore):
    async def create(self, feedback: FeedbackEvent) -> FeedbackEvent: ...
    async def get(self, feedback_id: UUID) -> FeedbackEvent | None: ...
    async def update(self, feedback: FeedbackEvent) -> FeedbackEvent: ...
    async def transition(self, feedback_id: UUID, status: FeedbackStatus) -> FeedbackEvent: ...
    async def query(self, query: FeedbackQuery) -> Sequence[FeedbackEvent]: ...
    async def list(self) -> Sequence[FeedbackEvent]: ...
```

### Custom handler

```python
from feedback_manager.contracts import FeedbackContext, FeedbackHandler, FeedbackHandlerResult
from feedback_manager.core import FeedbackEvent


class HumanReviewHandler(FeedbackHandler):
    async def handle(
        self, feedback: FeedbackEvent, context: FeedbackContext
    ) -> FeedbackHandlerResult:
        return FeedbackHandlerResult(handled=True, detail="queued for review")
```

## Documentation

The full documentation site lives under `docs/` and includes:

- architecture guides
- ADRs
- getting-started guides
- concept references
- integration guides
- API reference
- advanced extension guides
- reliability, security, testing, and FAQ pages

Published documentation URL (project metadata): <https://feedback-manager.readthedocs.io>

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for local development setup, running
the test suite/coverage, linting, type-checking, and building the docs site.

## Author and license

- Author: **S MUNI HARISH**
- License: **Apache License 2.0**
