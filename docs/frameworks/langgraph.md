# LangGraph human-in-the-loop and execution context

Use these LangGraph helpers to correlate feedback with graph execution and
to record the human decision around a native LangGraph interrupt.

The supported public imports are:

```python
from feedback_manager.integrations.langgraph import (
    HumanInTheLoopBridge,
    execution_context_from_config,
    extract_interrupts,
)
```

## Correlate feedback with a graph run

Use `execution_context_from_config()` when your node already receives a
LangGraph configuration. Pass the resulting context to `manager.submit()` so
the feedback can be queried by thread, run, checkpoint, or task.

```python
from feedback_manager import (
    FeedbackCategory,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.integrations.langgraph import execution_context_from_config

context = execution_context_from_config(config)

feedback = await manager.submit(
    source=FeedbackSource.APPLICATION,
    category=FeedbackCategory.VALIDATION,
    feedback_type="validation_failure",
    target=FeedbackTarget(
        type=FeedbackTargetType.NODE,
        id="validate_answer",
    ),
    execution_context=context,
)
```

## Record a human-in-the-loop decision

`HumanInTheLoopBridge` records the request and the human response around
LangGraph's native `interrupt()` / `Command(resume=...)` flow. LangGraph
continues to own pause, persistence, and resume behavior.

Typical flow:

1. the graph raises a native interrupt
2. the application presents the request to a human
3. `bridge.request(...)` records the pending request
4. `bridge.resolve(...)` records the decision
5. `bridge.resume_command(...)` creates the native LangGraph resume command

See [HITL approval](../examples/hitl-approval.md) for a complete compiled
graph with verified output, and
[deepagents + MCP + HITL](../examples/agent-mcp-deepagents.md) for the same
pattern around a real tool approval gate.

## Detect interrupts in streamed output

When your application consumes streamed graph output, use
`extract_interrupts()` to obtain native interrupt objects from a stream
chunk before presenting them to the human-facing channel.
