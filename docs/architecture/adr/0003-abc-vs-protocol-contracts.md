# ADR 0003: Use ABCs for stateful contracts and Protocols for structural ones

## Context

The package has multiple extension points with different needs. Some require behavioral guarantees; others only need a callable shape.

## Decision

Use ABCs for `FeedbackStore`, `FeedbackHandler`, and `FeedbackRouter`, and Protocols for correlators, serializers, provenance adapters, subscribers, and policy hooks.

## Consequences

- stateful contracts can document stronger invariants explicitly
- duck-typed integrations remain easy to implement
- plain async functions can serve as subscribers
- existing serializers/adapters can fit without forced inheritance

