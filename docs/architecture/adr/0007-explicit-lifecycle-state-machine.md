# ADR 0007: Represent lifecycle rules as an explicit state machine

## Context

Lifecycle code can easily become scattered across many conditional branches, making legal transitions hard to audit and test.

## Decision

Store legal transitions centrally in `LEGAL_TRANSITIONS` and validate them through `validate_transition()`.

## Consequences

- lifecycle behavior is visible in one place
- happy-path and illegal transitions are easy to test exhaustively
- same-state idempotency is explicit
- application methods like `resolve()` inherit consistent rules automatically
