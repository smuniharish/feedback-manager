# Dependency Matrix

The runtime dependency set is intentionally explicit. None of the core
framework capabilities are optional extras.

## Runtime dependencies

| Package | Constraint | Mandatory? | Why it is present |
| --- | --- | --- | --- |
| `langchain-core` | `>=1.6,<2` | Yes | Required for callback and tool-failure capture |
| `langgraph` | `>=1.2.11,<1.3` | Yes | Required for execution-context and human-in-the-loop helpers |
| `langgraph-xai` | `>=0.1.0,<0.2` | Yes | Sole provenance source; provenance is a first-class feature, not an optional plugin |
| `pydantic` | `>=2.12,<3` | Yes | Required for all domain models (`FeedbackEvent`, `FeedbackTarget`, contexts, provenance references) |

## Why the framework dependencies are not optional

### `langchain-core`

The package ships a concrete `FeedbackCallbackHandler` and `capture_tool_feedback()` helper. Those are not stubs or pseudo-code; they import and use real LangChain APIs.

### `langgraph`

The package ships public helpers around real LangGraph concepts:

- `execution_context_from_config()`
- `HumanInTheLoopBridge`
- `extract_interrupts()`

The documented examples use compiled graphs and native
`interrupt()` / `Command(resume=...)`.

### `langgraph-xai`

Provenance is not simulated or abstracted behind a user-selectable provider.
Applications pass their real `XAIRuntime` to `FeedbackManager`, which can
correlate feedback with live or stored execution provenance.

## Library design implication

Because the integrations are thin and concrete rather than optional plug-in shims, downstream users get:

- a single install path
- no conditional imports in normal use
- documentation that can assume the integrations exist
- real integration tests instead of mocked optional behavior

The trade-off is intentional: the package is opinionated about the LangChain/LangGraph/`langgraph-xai` ecosystem.
