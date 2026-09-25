# Integration

Source of truth: the
[Getting started](https://feedback-manager.readthedocs.io/en/latest/getting-started/quickstart/),
[Framework guides](https://feedback-manager.readthedocs.io/en/latest/frameworks/langchain/),
and [Examples](https://feedback-manager.readthedocs.io/en/latest/examples/human-correction/)
docs, and the
[`examples/`](https://github.com/smuniharish/feedback-manager/tree/master/examples)
directory. Prefer the actual example script closest to the workload over
re-deriving code from memory.

## Minimal quickstart (root package only)

```python
import asyncio

from feedback_manager import (
    FeedbackCategory,
    ExecutionContext,
    FeedbackManager,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)


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

    print(resolved.status)  # resolved
    print(resolved.metadata["resolution"])  # {'applied': True, 'channel': 'manual_review'}


asyncio.run(main())
```

See the
[Quickstart](https://feedback-manager.readthedocs.io/en/latest/getting-started/quickstart/)
and
[`examples/01_human_correction.py`](https://github.com/smuniharish/feedback-manager/blob/master/examples/01_human_correction.py).

## LangChain: capture callback failures and tool failures

Supported public imports:

```python
from feedback_manager.integrations.langchain import (
    FeedbackCallbackHandler,
    capture_tool_feedback,
)
```

Attach one `FeedbackCallbackHandler` per manager through LangChain's normal
`callbacks=[...]` configuration; LangChain still owns execution and callback
dispatch, the handler only records failures as feedback (classifying
timeouts as `timeout`, cancellations as `cancellation`, other exceptions as
`failure`). The original exception still propagates — capture does not
replace error handling.

```python
handler = FeedbackCallbackHandler(manager)
await fetch_weather.ainvoke({"city": "Canberra"}, config={"callbacks": [handler]})
```

For tool code invoked outside a callback-enabled runnable, wrap the call:

```python
async with capture_tool_feedback(manager, tool_call_id="call-123"):
    await my_tool()
```

See
[LangChain callbacks and tools](https://feedback-manager.readthedocs.io/en/latest/frameworks/langchain/),
[Tool failure example](https://feedback-manager.readthedocs.io/en/latest/examples/tool-failure/),
and
[`examples/03_tool_failure.py`](https://github.com/smuniharish/feedback-manager/blob/master/examples/03_tool_failure.py).

## LangGraph: execution context and human-in-the-loop

Supported public imports:

```python
from feedback_manager.integrations.langgraph import (
    HumanInTheLoopBridge,
    execution_context_from_config,
    extract_interrupts,
)
```

Correlate feedback with a graph run using the node's own LangGraph `config`:

```python
context = execution_context_from_config(config)

feedback = await manager.submit(
    source=FeedbackSource.APPLICATION,
    category=FeedbackCategory.VALIDATION,
    feedback_type="validation_failure",
    target=FeedbackTarget(type=FeedbackTargetType.NODE, id="validate_answer"),
    execution_context=context,
)
```

Record a human decision around LangGraph's native `interrupt()` /
`Command(resume=...)` flow with `HumanInTheLoopBridge` — LangGraph keeps
owning pause, persistence, and resume:

1. the graph raises a native interrupt;
2. the application presents the request to a human;
3. `bridge.request(...)` records the pending request;
4. `bridge.resolve(...)` records the decision;
5. `bridge.resume_command(...)` builds the native LangGraph resume command.

Use `extract_interrupts()` to pull native interrupt objects out of a stream
chunk before presenting them to a human-facing channel.

See
[LangGraph human-in-the-loop](https://feedback-manager.readthedocs.io/en/latest/frameworks/langgraph/),
[HITL approval example](https://feedback-manager.readthedocs.io/en/latest/examples/hitl-approval/),
[`examples/02_hitl_approval.py`](https://github.com/smuniharish/feedback-manager/blob/master/examples/02_hitl_approval.py),
and
[`examples/08_agent_deepagents_mcp.py`](https://github.com/smuniharish/feedback-manager/blob/master/examples/08_agent_deepagents_mcp.py)
for the same pattern around a real tool approval gate.

## Provenance (langgraph-xai)

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

See
[Provenance concept](https://feedback-manager.readthedocs.io/en/latest/concepts/provenance/),
[Provenance example](https://feedback-manager.readthedocs.io/en/latest/examples/provenance/),
and
[`examples/06_provenance.py`](https://github.com/smuniharish/feedback-manager/blob/master/examples/06_provenance.py).

## Matching an example to a workload

| Workload | Example |
| --- | --- |
| Human correction | [`examples/01_human_correction.py`](https://github.com/smuniharish/feedback-manager/blob/master/examples/01_human_correction.py) |
| HITL approval around a native interrupt | [`examples/02_hitl_approval.py`](https://github.com/smuniharish/feedback-manager/blob/master/examples/02_hitl_approval.py) |
| Tool failure via callbacks | [`examples/03_tool_failure.py`](https://github.com/smuniharish/feedback-manager/blob/master/examples/03_tool_failure.py) |
| Generation interruption / partial result | [`examples/04_generation_interruption.py`](https://github.com/smuniharish/feedback-manager/blob/master/examples/04_generation_interruption.py) |
| Evaluator/LLM-as-judge feedback | [`examples/05_evaluator_feedback.py`](https://github.com/smuniharish/feedback-manager/blob/master/examples/05_evaluator_feedback.py) |
| Provenance with `langgraph-xai` | [`examples/06_provenance.py`](https://github.com/smuniharish/feedback-manager/blob/master/examples/06_provenance.py) |
| Full agent with MCP tools via `create_agent` | [`examples/07_agent_mcp_create_agent.py`](https://github.com/smuniharish/feedback-manager/blob/master/examples/07_agent_mcp_create_agent.py) |
| Full agent with `deepagents` + MCP + HITL | [`examples/08_agent_deepagents_mcp.py`](https://github.com/smuniharish/feedback-manager/blob/master/examples/08_agent_deepagents_mcp.py) |
| Overriding manager defaults (custom store/router/policy) | [`examples/09_override_defaults.py`](https://github.com/smuniharish/feedback-manager/blob/master/examples/09_override_defaults.py) |
| Full feedback matrix in one run | [`examples/10_full_matrix_feedback.py`](https://github.com/smuniharish/feedback-manager/blob/master/examples/10_full_matrix_feedback.py) |
| Grafana observability dashboard | [`examples/11_grafana_dashboard.py`](https://github.com/smuniharish/feedback-manager/blob/master/examples/11_grafana_dashboard.py) |
| Postgres-backed store at scale | [`examples/12_organic_scenarios_postgres.py`](https://github.com/smuniharish/feedback-manager/blob/master/examples/12_organic_scenarios_postgres.py) |
