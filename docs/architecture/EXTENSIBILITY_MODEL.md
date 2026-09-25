# Extensibility Model

Extension points are published through `feedback_manager.contracts`.

## Design rule: ABC vs Protocol

The package uses two styles intentionally.

### Protocols for structural, duck-typed integration points

Use a `Protocol` when the shape matters more than inheritance and there are no shared invariants to enforce.

Examples:

- `FeedbackSubscriber`
- `FeedbackSerializer`
- `FeedbackCorrelator`

Why:

- plain async functions can be subscribers
- pydantic-based serializers already satisfy serializer shape
- external systems can adapt without subclassing package types

### ABCs for stateful or behavior-constrained components

Use an `ABC` when the contract carries stronger operational expectations.

Examples:

- `FeedbackStore`
- `FeedbackHandler`
- `FeedbackRouter`
- `FeedbackLifecyclePolicy`
- `FeedbackPolicy`

Why:

- stores must be concurrency-safe and honor idempotency semantics
- handlers have a standard async `handle()` contract with structured result type
- routers must be side-effect free selectors returning handlers
- lifecycle/redaction policies enforce business-critical invariants
  (authorization, data handling) where an explicit, non-duck-typed
  contract makes the extension point harder to satisfy accidentally

## Actual contracts

### ABCs

#### `FeedbackStore`

Methods:

- `create(feedback)`
- `get(feedback_id)`
- `update(feedback)`
- `transition(feedback_id, status)`
- `query(query)`
- `list()`

#### `FeedbackHandler`

Method:

- `handle(feedback, context) -> FeedbackHandlerResult`

Related dataclasses:

- `FeedbackContext`
- `FeedbackHandlerResult`

#### `FeedbackRouter`

Method:

- `route(feedback) -> Sequence[FeedbackHandler]`

#### `FeedbackLifecyclePolicy`

Method:

- `authorize_transition(feedback, target) -> None`

#### `FeedbackPolicy`

Method:

- `apply(feedback) -> FeedbackEvent`

### Protocols

#### `FeedbackCorrelator`

- `correlate(feedback, execution_context) -> CorrelationContext`

#### `FeedbackSerializer`

- `serialize(feedback) -> dict[str, Any]`
- `deserialize(data) -> FeedbackEvent`

Bundled implementation:

- `DefaultFeedbackSerializer`

#### `FeedbackSubscriber`

- `async __call__(feedback) -> None`

## Practical extension guidance

- implement `FeedbackStore` for durable persistence
- implement `FeedbackHandler` for side effects or downstream workflows
- implement `FeedbackRouter` for custom dispatch rules
- implement `FeedbackPolicy` for redaction or metadata filtering
- provenance is not a generic extension point: `langgraph-xai` is the
  mandatory, sole supported provenance source; configure it by passing
  `XAIRuntime` to `FeedbackManager` (see
  [PROVENANCE_MODEL.md](PROVENANCE_MODEL.md))
