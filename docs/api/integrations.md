# Integrations

## LangChain

`feedback_manager.integrations.langchain` records failures reported through
LangChain's callback system, and failures of tool calls made outside it.

::: feedback_manager.integrations.langchain.FeedbackCallbackHandler
    options:
      members: false

::: feedback_manager.integrations.langchain.capture_tool_feedback

::: feedback_manager.integrations.langchain.category_for_error

## LangGraph

`feedback_manager.integrations.langgraph` builds execution contexts from
LangGraph objects and records human-in-the-loop interrupts.

::: feedback_manager.integrations.langgraph.execution_context_from_config

::: feedback_manager.integrations.langgraph.execution_context_from_snapshot

::: feedback_manager.integrations.langgraph.HumanInTheLoopBridge

::: feedback_manager.integrations.langgraph.extract_interrupts

::: feedback_manager.integrations.langgraph.INTERRUPT_KEY

## langgraph-xai

::: feedback_manager.integrations.xai.XAIProvenanceAdapter
