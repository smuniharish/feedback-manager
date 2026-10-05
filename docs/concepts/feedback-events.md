# Feedback events

Every piece of feedback is a `FeedbackEvent`: an immutable, validated record of
who sent it, what kind of feedback it is, what it is about, and where in an
execution it happened.

```python
from feedback_manager import ExecutionContext, FeedbackTarget, FeedbackTargetType

feedback = await manager.submit(
    source="human",                                   # who
    category="correction",                            # what kind
    target=FeedbackTarget(                            # about what
        type=FeedbackTargetType.GENERATION, id="gen-42"
    ),
    payload={"corrected_text": "Canberra is the capital of Australia."},
    execution_context=ExecutionContext(               # where
        thread_id="support-7", run_id="run-901", node_id="answer"
    ),
)
```

## Fields

| Field | Set by | Description |
|---|---|---|
| `feedback_id` | Generated | A unique UUID. |
| `source` | You | Who or what produced the feedback. |
| `category` | You | What kind of feedback it is. |
| `feedback_type` | You (optional) | An application-defined subtype, such as `"tool_error"`. |
| `target` | You | What the feedback is about: a target type, an ID, and optional metadata. |
| `payload` | You (optional) | The feedback content. |
| `metadata` | You (optional) | Application-defined details. |
| `execution_context` | You (optional) | The execution the target belongs to. |
| `idempotency_key` | You (optional) | Deduplicates retried submissions. |
| `correlation_id` | The correlator | Groups related feedback. See [correlation](correlation.md). |
| `provenance` | The provenance adapter | Links to `langgraph-xai` records. See [provenance](provenance.md). |
| `status` | The lifecycle | See [lifecycle](lifecycle.md). |
| `resolution` | `resolve`, `reject`, `cancel` | How the feedback was closed. |
| `created_at`, `updated_at` | Generated | Timezone-aware timestamps (UTC). |

## Sources

The source says who or what produced the feedback.

| Constant | Value | Typical use |
|---|---|---|
| `FeedbackSource.HUMAN` | `human` | An end user or a reviewer. |
| `FeedbackSource.AGENT` | `agent` | An agent or graph reporting on its own execution. |
| `FeedbackSource.GENERATION` | `generation` | A model generation, such as a failed or interrupted call. |
| `FeedbackSource.TOOL` | `tool` | A tool or retriever. |
| `FeedbackSource.EVALUATOR` | `evaluator` | An LLM-as-judge or a rule-based checker. |
| `FeedbackSource.APPLICATION` | `application` | Your application's own code. |
| `FeedbackSource.SYSTEM` | `system` | Infrastructure, such as a human-in-the-loop request. |
| `FeedbackSource.EXTERNAL` | `external` | A third-party system, such as a ticketing tool. |

## Categories

The category says what kind of feedback it is, independently of its source: a
timeout can come from a tool, a person, or the system alike.

| Constant | Value | Meaning |
|---|---|---|
| `APPROVAL` | `approval` | Approval of an action or output. |
| `REJECTION` | `rejection` | Rejection of an action or output. |
| `CORRECTION` | `correction` | A corrected version of an output. |
| `RATING` | `rating` | A score or rating. |
| `COMMENT` | `comment` | A free-form remark. |
| `INTERRUPTION` | `interruption` | Work stopped before it finished. |
| `CANCELLATION` | `cancellation` | Work that was cancelled. |
| `FAILURE` | `failure` | An error. |
| `TIMEOUT` | `timeout` | Work that exceeded its time limit. |
| `VALIDATION` | `validation` | The result of a validation check. |
| `QUALITY` | `quality` | An assessment of quality, typically from an evaluator. |
| `UNCERTAINTY` | `uncertainty` | Low confidence in an output. |
| `REQUEST_FOR_HUMAN` | `request_for_human` | A request for a person's input. |
| `PARTIAL_RESULT` | `partial_result` | An incomplete output. |
| `COMPLETION` | `completion` | Work that finished. |

The constants live on `FeedbackCategory`, for example `FeedbackCategory.TIMEOUT`.

## Targets

A `FeedbackTarget` names the thing the feedback is about: a target type and
the ID of that thing, plus optional JSON metadata.

| Constant | Value | Identifies |
|---|---|---|
| `APPLICATION` | `application` | The application as a whole. |
| `AGENT` | `agent` | An agent. |
| `GRAPH` | `graph` | A LangGraph graph. |
| `THREAD` | `thread` | A conversation thread (a LangGraph `thread_id`). |
| `RUN` | `run` | One execution of a graph or chain. |
| `CHECKPOINT` | `checkpoint` | A LangGraph checkpoint. |
| `NODE` | `node` | A graph node. |
| `TASK` | `task` | A LangGraph task. |
| `TOOL_CALL` | `tool_call` | One call of a tool. |
| `TOOL_RESULT` | `tool_result` | The result a tool call returned. |
| `GENERATION` | `generation` | One model generation. |
| `MESSAGE` | `message` | One message. |
| `STATE` | `state` | Graph state. |

The constants live on `FeedbackTargetType`, for example `FeedbackTargetType.TOOL_CALL`.

## Open values

Sources, categories, and target types are *open*: the constants above are the
well-known values, and any other non-empty string is valid too. No subclassing
or registration is needed.

```python
from feedback_manager import FeedbackSource

source = FeedbackSource("mcp_server")
assert source == "mcp_server"           # behaves exactly like the string
assert FeedbackSource.HUMAN == "human"  # constants too

FeedbackSource.known_values()           # the 8 well-known sources, in order
```

Values are strings in every respect: they compare, hash, and serialize as the
string they wrap, so you can pass `"human"` or `FeedbackSource.HUMAN`
interchangeably. Empty values and values with leading or trailing whitespace
are rejected.

## Execution context

`ExecutionContext` identifies the execution the target belongs to. Every field
is optional, because feedback stays useful with partial context.

| Field | Identifies |
|---|---|
| `application_id`, `tenant_id`, `graph_id` | The application, tenant, and graph, as `langgraph-xai` names them. |
| `thread_id` | The LangGraph thread. |
| `run_id` | The run. Inside a `langgraph-xai` instrumented call, the `langgraph-xai` run ID. |
| `checkpoint_id` | The LangGraph checkpoint. |
| `node_id`, `task_id` | The graph node and task. |
| `message_id`, `tool_call_id`, `generation_id` | A message, tool call, or model generation. |
| `interrupt_id` | The LangGraph interrupt the feedback answers. |
| `metadata` | JSON details about the execution. |

You rarely build it by hand: the integrations fill it from the objects LangGraph
already gives you.

```python
from langchain_core.runnables import RunnableConfig

from feedback_manager.integrations.langgraph import (
    execution_context_from_config,
    execution_context_from_snapshot,
)


async def answer(state: State, config: RunnableConfig) -> State:
    context = execution_context_from_config(config)  # inside a node
    ...


snapshot = await graph.aget_state(config)
context = execution_context_from_snapshot(snapshot)  # after a graph paused
```

## Validation rules

- Identifiers (target IDs, context IDs, idempotency keys, correlation IDs,
  `feedback_type`) are non-empty strings without leading or trailing whitespace.
- `payload`, `metadata`, `target.metadata`, and `resolution` are JSON objects:
  string keys and JSON values. Non-finite numbers (`NaN`, `Infinity`) are
  rejected, so every event survives a JSON round trip.
- Timestamps are timezone-aware.

`FeedbackManager` reports invalid input as `FeedbackValidationError`. Building
a model directly raises pydantic's `ValidationError`; both are `ValueError`s.

## Immutability and serialization

Events are frozen pydantic models. Lifecycle changes produce new copies through
`FeedbackEvent.with_status`, so an event you hold never changes underneath you,
and events can be shared across tasks and threads without locks.

Events serialize to JSON and back without loss:

```python
from feedback_manager import FeedbackEvent

data = feedback.model_dump(mode="json")                       # a JSON-ready dict
restored = FeedbackEvent.model_validate_json(feedback.model_dump_json())
assert restored == feedback
```
