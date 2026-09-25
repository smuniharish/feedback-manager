# ADR 0006: Abstract persistence behind `FeedbackStore`

## Context

Different applications need different storage backends, retention rules, and access-control behavior.

## Decision

Define `FeedbackStore` as an abstract contract and ship only `InMemoryFeedbackStore` as the bundled implementation.

## Consequences

- the package is usable out of the box for tests and examples
- production systems can implement durable storage without forking the manager
- security and retention policies can live in store implementations appropriate to each application

