# Extensibility Model

The extension points live in `src/feedback_manager/contracts/`.

## Design rule: ABC vs Protocol

The package uses two styles intentionally.

### Protocols for structural, duck-typed integration points

Use a `Protocol` when the shape matters more than inheritance and there are no shared invariants to enforce.

Examples:

- `FeedbackSubscriber`
- `FeedbackSerializer`
- `FeedbackCorrelator`
- `FeedbackProvenanceAdapter`
- `FeedbackLifecyclePolicy`
- `FeedbackPolicy`

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

Why:

- stores must be concurrency-safe and honor idempotency semantics
- handlers have a standard async `handle()` contract with structured result type
- routers must be side-effect free selectors returning handlers

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

### Protocols

#### `FeedbackCorrelator`

- `correlate(feedback, execution_context) -> CorrelationContext`

#### `FeedbackProvenanceAdapter`

- `resolve(correlation) -> FeedbackProvenanceReference | None`

#### `FeedbackSerializer`

- `serialize(feedback) -> dict[str, Any]`
- `deserialize(data) -> FeedbackEvent`

Bundled implementation:

- `DefaultFeedbackSerializer`

#### `FeedbackSubscriber`

- `async __call__(feedback) -> None`

#### `FeedbackLifecyclePolicy`

- `authorize_transition(feedback, target) -> None`

#### `FeedbackPolicy`

- `apply(feedback) -> FeedbackEvent`

## Practical extension guidance

- implement `FeedbackStore` for durable persistence
- implement `FeedbackHandler` for side effects or downstream workflows
- implement `FeedbackRouter` for custom dispatch rules
- implement `FeedbackPolicy` for redaction or metadata filtering
- implement `FeedbackProvenanceAdapter` if provenance comes from a system other than `langgraph-xai`

