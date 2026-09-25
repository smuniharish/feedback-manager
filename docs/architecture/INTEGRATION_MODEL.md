# Integration Model

The package ships three integration boundaries under `src/feedback_manager/integrations/`.

## Design principle

Each integration is a **thin adapter** around a framework that already owns the underlying behavior.

## LangChain integration

Files:

- `integrations/langchain/adapter.py`
- `integrations/langchain/callbacks.py`
- `integrations/langchain/tools.py`

Responsibilities:

- map exceptions to feedback categories with `category_for_error()`
- translate real LangChain callback errors into feedback events through `FeedbackCallbackHandler`
- capture tool failures outside callback chains with `capture_tool_feedback()`

Not owned here:

- runnable execution
- callback dispatch mechanism itself
- tool invocation

## LangGraph integration

Files:

- `integrations/langgraph/adapter.py`
- `integrations/langgraph/interrupt.py`
- `integrations/langgraph/streaming.py`

Responsibilities:

- extract `ExecutionContext` from `RunnableConfig`
- bridge native interrupt/resume flows to feedback records with `HumanInTheLoopBridge`
- detect LangGraph `__interrupt__` payloads with `extract_interrupts()`

Not owned here:

- graph compilation
- checkpointing
- interrupt runtime
- stream transport

## `langgraph-xai` integration

Files:

- `integrations/xai/adapter.py`

Responsibilities:

- resolve provenance from `XAIRuntime.current_run`
- fall back to `registry.get(ProvenanceStore)` by `run_id`
- map `Execution` data into `FeedbackProvenanceReference`

Not owned here:

- provenance capture
- execution storage
- instrumentation runtime

## Resulting architecture

Because the integrations are kept thin:

- the core domain stays framework-independent
- framework upgrades are localized
- applications can use only the parts they need while still having one coherent feedback model

