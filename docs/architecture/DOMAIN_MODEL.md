# Domain Model

The feedback domain is exposed through stable public model types.

## `FeedbackEvent`

`FeedbackEvent` is the central immutable-by-convention model.

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `feedback_id` | `UUID` | Defaults to `uuid4()` |
| `idempotency_key` | `str \| None` | Used by stores for deduplication |
| `source` | `FeedbackSource` | Who or what produced the feedback |
| `category` | `FeedbackCategory` | What kind of feedback it is |
| `feedback_type` | `str \| None` | Optional application-defined subtype such as `generation_started` |
| `target` | `FeedbackTarget` | What the feedback is about |
| `payload` | `dict[str, Any]` | Arbitrary structured content |
| `execution_context` | `ExecutionContext \| None` | Execution identifiers attached directly to the event |
| `correlation` | `CorrelationContext \| None` | Derived linking context |
| `provenance` | `FeedbackProvenanceReference \| None` | Upstream provenance pointer |
| `status` | `FeedbackStatus` | Defaults to `CREATED` |
| `created_at` | `datetime` | UTC-aware timestamp |
| `updated_at` | `datetime` | UTC-aware timestamp |
| `metadata` | `dict[str, Any]` | Extra application metadata |

### Mutation style

The model is `frozen=True`. Updates happen by returning copies:

- `with_status(status)`
- `with_correlation(correlation)`
- `with_provenance(provenance)`

## `FeedbackSource`

`FeedbackSource` is an **open** string value, not a closed enum. Built-in well-known values:

- `HUMAN`
- `AGENT`
- `GENERATION`
- `TOOL`
- `EVALUATOR`
- `APPLICATION`
- `SYSTEM`
- `EXTERNAL`

Applications may construct new values such as `FeedbackSource("mcp_server")`.

## `FeedbackCategory`

Also an open string value. Built-in well-known categories:

- `APPROVAL`
- `REJECTION`
- `CORRECTION`
- `RATING`
- `COMMENT`
- `INTERRUPTION`
- `CANCELLATION`
- `FAILURE`
- `TIMEOUT`
- `VALIDATION`
- `QUALITY`
- `UNCERTAINTY`
- `REQUEST_FOR_HUMAN`
- `PARTIAL_RESULT`
- `COMPLETION`

## `FeedbackTargetType` and `FeedbackTarget`

### `FeedbackTargetType`

Built-in well-known target kinds:

- `APPLICATION`
- `AGENT`
- `GRAPH`
- `THREAD`
- `RUN`
- `CHECKPOINT`
- `NODE`
- `TASK`
- `TOOL_CALL`
- `TOOL_RESULT`
- `GENERATION`
- `MESSAGE`
- `STATE`

Like source/category, this is open and extensible.

### `FeedbackTarget`

`FeedbackTarget` is a frozen pydantic model with:

| Field | Type | Notes |
| --- | --- | --- |
| `type` | `FeedbackTargetType` | Kind of thing being discussed |
| `id` | `str` | Opaque identifier string |
| `metadata` | `dict[str, Any]` | Extra target-scoped metadata |

## `ExecutionContext`

This is a thin, framework-independent bag of execution identifiers:

- `application_id`
- `tenant_id`
- `graph_id`
- `thread_id`
- `run_id`
- `checkpoint_id`
- `node_id`
- `task_id`
- `message_id`
- `tool_call_id`
- `generation_id`
- `metadata`

It also provides `is_empty()`.

## `CorrelationContext`

Fields:

| Field | Type |
| --- | --- |
| `correlation_id` | `str \| None` |
| `parent_feedback_id` | `UUID \| None` |
| `related_feedback_ids` | `tuple[UUID, ...]` |
| `execution` | `ExecutionContext \| None` |
| `metadata` | `dict[str, Any]` |

`FeedbackManager` uses a correlator to derive this at submission time.

## `FeedbackProvenanceReference`

This is the small framework-independent shape used by the rest of the package. `langgraph-xai` is the mandatory, sole provenance source, so there is no `provider` field to disambiguate between multiple providers:

| Field | Type |
| --- | --- |
| `execution_id` | `str \| None` |
| `decision_id` | `str \| None` |
| `evidence_ids` | `tuple[str, ...]` |
| `human_interaction_id` | `str \| None` |
| `tool_execution_id` | `str \| None` |
| `summary` | `str \| None` |
| `metadata` | `dict[str, Any]` |

The package populates execution/tool/human summary fields and metadata from
`langgraph-xai`; `decision_id` and `evidence_ids` are part of the stable
shape but are not currently populated.

## `FeedbackStatus`

This is a closed `StrEnum`, unlike source/category/target types.

States:

- `CREATED`
- `RECEIVED`
- `ACKNOWLEDGED`
- `HANDLED`
- `RESOLVED`
- `REJECTED`
- `CANCELLED`
- `EXPIRED`

Terminal statuses are collected in `TERMINAL_STATUSES`:

- `RESOLVED`
- `REJECTED`
- `CANCELLED`
- `EXPIRED`
