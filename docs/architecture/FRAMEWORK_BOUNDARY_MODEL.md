# Framework Boundary Model

The package exposes two application-facing framework guides and one mandatory
provenance boundary.

## Design principle

Each boundary is thin: the upstream framework continues to own execution.

## LangChain boundary

Responsibilities:

- map exceptions to feedback categories with `category_for_error()`
- translate real LangChain callback errors into feedback events through `FeedbackCallbackHandler`
- capture tool failures outside callback chains with `capture_tool_feedback()`

Not owned here:

- runnable execution
- callback dispatch mechanism itself
- tool invocation

## LangGraph boundary

Responsibilities:

- extract `ExecutionContext` from `RunnableConfig`
- bridge native interrupt/resume flows to feedback records with `HumanInTheLoopBridge`
- detect LangGraph `__interrupt__` payloads with `extract_interrupts()`

Not owned here:

- graph compilation
- checkpointing
- interrupt runtime
- stream transport

## Mandatory provenance boundary

`langgraph-xai` is not an optional integration or a customization point. It is
the sole provenance provider. Applications supply the same `XAIRuntime` used
to instrument their graph to `FeedbackManager`.

Responsibilities:

- correlate feedback with available `langgraph-xai` execution context
- represent that correlation as `FeedbackProvenanceReference`

Not owned here:

- provenance capture
- execution storage
- instrumentation runtime

## Resulting architecture

Because the framework boundaries are kept thin:

- the core domain stays framework-independent
- framework upgrades are localized
- applications opt into the LangChain/LangGraph helpers they need while
  provenance remains consistently backed by `langgraph-xai`
