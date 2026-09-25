# Routing Model

Routing is defined by:

- the `FeedbackRouter` ABC
- `RoutingRule` and predicate helpers
- `DefaultFeedbackRouter`
- the `FeedbackHandler` ABC

## Routing responsibilities

Routing decides **which handlers should receive an event**. It does not execute handlers; `FeedbackManager` does that so it can isolate failures per handler.

## `RoutingRule`

Each rule contains:

- `predicate: Callable[[FeedbackEvent], bool]`
- `handlers: tuple[FeedbackHandler, ...]`
- `name: str = "rule"`

Predicate helpers bundled with the package:

- `by_source(source)`
- `by_category(category)`
- `by_target_type(target_type)`
- `any_of(*predicates)`
- `all_of(*predicates)`

## `DefaultFeedbackRouter`

The default router:

1. evaluates rules in order
2. collects handlers from every matching rule
3. de-duplicates handlers by object identity
4. if nothing matched, returns `default_handlers`

Important behavior:

- multiple rules may contribute handlers
- the same handler object only runs once per event
- if no rule matches and no defaults are configured, routing returns an empty sequence

## Manager behavior

During `_route()`:

1. `FeedbackManager` asks the router for handlers
2. builds a `FeedbackContext` using `feedback.correlation`
3. invokes handlers sequentially
4. isolates each handler call through the configured `FailurePolicy`
5. emits `feedback.routed` observability data with `handler_count`

## Out-of-scope routing behavior

The router does not:

- mutate feedback
- persist routing state
- run handlers concurrently
- make business decisions for the application

Applications that need richer logic can implement `FeedbackRouter` directly.
