# Writing a framework adapter

## Contents

- [What an adapter is](#what-an-adapter-is)
- [The three required parts](#the-three-required-parts)
- [Failure capture](#failure-capture)
- [Provenance](#provenance)
- [Packaging](#packaging)

## What an adapter is

A `FrameworkAdapter` translates one agent framework's types into
feedback-manager's: where an execution is, what a pause asks, how to resume,
and how to show learned context. The core never imports a framework; the
bundled adapters are `feedback_manager.integrations.frameworks.langchain`
(LangChain and LangGraph, one adapter) and `.custom` (plain Python, no
dependency). Start from `CustomAgentAdapter` if the framework passes plain
dictionaries; it may need no adapter at all.

An adapter observes. It never changes inputs, outputs, state, retries, or
control flow, and it holds no state: every method returns a new object.

## The three required parts

| Method | Returns | Implement |
|---|---|---|
| `context_extractor()` | `ExecutionContextExtractor[Source]` | `extract(source, *, node_id=None, tool_call_id=None, generation_id=None, message_id=None, interrupt_id=None)`: map the framework's run, session, agent, and step identifiers onto `ExecutionContext` (`thread_id`, `run_id`, `agent_id`, `node_id`, ...). Explicit keyword arguments win. |
| `human_in_the_loop(manager)` | `HumanInTheLoopBridge[Interrupt, Resume]` | `interrupt_id(interrupt)`, `interrupt_value(interrupt)`, `resume_command(response, *, interrupt_id=None)`. The base class implements `request` and `resolve`. |
| `context_injector(retriever)` | `LearnedContextInjector[Rendered]` | `render(result)`: wrap `render_learned_context(result)` in the framework's message type. Or return `TextContextInjector(retriever)` for plain text. |

Also set the class attribute `name`, and override `failure_classifier()` when
the framework raises control-flow exceptions (pauses, handoffs) that are not
failures: return a `FailureClassifier` subclass whose `is_reportable` returns
`False` for them.

Record a pause after the framework has paused, never inside the step that
pauses: frameworks that replay a step on resume would record it twice.

## Failure capture

If the framework has callbacks or hooks for errors, translate them into
`manager.submit(...)` calls with `source="tool"` or `"model"`, the category
from the classifier, and the extractor's context. If it has none, wrap calls
in `async with capture_tool_feedback(manager, tool_call_id=..., tool_name=...,
execution_context=...)` from `feedback_manager.integrations`. The original
exception propagates after recording.

## Provenance

Provenance is separate from the adapter. Pass
`FeedbackManager(provenance_provider=AgxaiProvenanceProvider(runtime))`, where
`runtime` is the agxai `XAIRuntime` that instruments the framework (an agxai
`FrameworkInstrumentor` for frameworks other than LangGraph). Put the agxai
run ID in `ExecutionContext.run_id` so feedback links to it.

## Packaging

Ship an adapter for a third-party framework as an optional extra, so the core
stays dependency-free:

```toml
[project.optional-dependencies]
myframework = ["myframework>=2.1,<3"]  # the range you test against
```

Import the framework only inside the adapter module, and raise an
`ImportError` naming the extra when it is missing.
