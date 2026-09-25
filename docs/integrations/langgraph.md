# LangGraph integration

Files:

- `integrations/langgraph/adapter.py`
- `integrations/langgraph/interrupt.py`
- `integrations/langgraph/streaming.py`

## Execution context extraction

`execution_context_from_config()` reads a LangGraph or LangChain `RunnableConfig`-shaped mapping and maps it into `ExecutionContext`.

It understands:

- `configurable.thread_id`
- `configurable.run_id`
- `configurable.checkpoint_id`
- `configurable.task_id`
- metadata keys used by `langgraph-xai`

## Human-in-the-loop bridge

`HumanInTheLoopBridge` wraps a `FeedbackManager` for native LangGraph interrupt/resume flows.

Key methods:

- `request(...)`
- `resolve(...)`
- `resume_command(response)`
- `interrupt(prompt)`

This does not replace LangGraph's interrupt engine. It records feedback *around* it.

## Stream interrupt detection

`extract_interrupts()` recognizes LangGraph's `__interrupt__` chunks when using streaming APIs that emit them.

## Example

See `examples/02_hitl_approval.py` for a real compiled graph using:

- `interrupt()`
- `Command(resume=...)`
- `InMemorySaver`
- `HumanInTheLoopBridge`

