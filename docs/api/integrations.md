# Integrations

Integrations are adapters around the core. The core never imports an agent
framework; each framework family lives in its own package under
`feedback_manager.integrations.frameworks` and implements the
framework-neutral contracts in `feedback_manager.integrations.base`.

| Module | Needs | Purpose |
| --- | --- | --- |
| `feedback_manager.integrations` | nothing | Framework-neutral contracts and `capture_tool_feedback`. |
| `feedback_manager.integrations.frameworks.custom` | nothing | Adapter for custom Python agents and multi-agent systems. |
| `feedback_manager.integrations.frameworks.langchain` | `feedback-manager[langchain]` | Adapter for LangChain and LangGraph. |
| `feedback_manager.integrations.provenance` | nothing (`agxai` is a core dependency) | Links feedback to `agxai` provenance. |

## Framework contracts

::: feedback_manager.integrations.base.FrameworkAdapter

::: feedback_manager.integrations.base.ExecutionContextExtractor

::: feedback_manager.integrations.base.FailureClassifier

::: feedback_manager.integrations.base.HumanInTheLoopBridge

::: feedback_manager.integrations.base.LearnedArtifactRetriever

::: feedback_manager.integrations.base.LearnedContextInjector

::: feedback_manager.integrations.base.TextContextInjector

::: feedback_manager.integrations.base.render_learned_context

::: feedback_manager.integrations.base.LEARNED_CONTEXT_HEADER

## Framework-neutral tool capture

::: feedback_manager.integrations.tools.capture_tool_feedback

## Custom agents

`feedback_manager.integrations.frameworks.custom` implements every contract
with plain Python types and needs no extra dependency.

::: feedback_manager.integrations.frameworks.custom.CustomAgentAdapter

::: feedback_manager.integrations.frameworks.custom.CustomContextExtractor

::: feedback_manager.integrations.frameworks.custom.CustomHumanInTheLoopBridge

::: feedback_manager.integrations.frameworks.custom.PendingInterrupt

::: feedback_manager.integrations.frameworks.custom.ResumeValue

## LangChain and LangGraph

`feedback_manager.integrations.frameworks.langchain` covers both LangChain and
LangGraph, which share one runtime (`langchain-core`). Install it with
`pip install "feedback-manager[langchain]"`.

::: feedback_manager.integrations.frameworks.langchain.LangChainAdapter

::: feedback_manager.integrations.frameworks.langchain.FeedbackCallbackHandler
    options:
      members: false

::: feedback_manager.integrations.frameworks.langchain.capture_tool_feedback

::: feedback_manager.integrations.frameworks.langchain.category_for_error

::: feedback_manager.integrations.frameworks.langchain.LangChainFailureClassifier

::: feedback_manager.integrations.frameworks.langchain.LangChainContextExtractor

::: feedback_manager.integrations.frameworks.langchain.execution_context_from_config

::: feedback_manager.integrations.frameworks.langchain.execution_context_from_snapshot

::: feedback_manager.integrations.frameworks.langchain.LangGraphHumanInTheLoopBridge

::: feedback_manager.integrations.frameworks.langchain.extract_interrupts

::: feedback_manager.integrations.frameworks.langchain.INTERRUPT_KEY

::: feedback_manager.integrations.frameworks.langchain.LangChainContextInjector

## Provenance

::: feedback_manager.integrations.provenance.AgxaiProvenanceProvider
