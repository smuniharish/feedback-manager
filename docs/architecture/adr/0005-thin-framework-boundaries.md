# ADR 0005: Keep framework boundaries thin and localized

## Context

Framework-specific code changes faster than the core feedback domain.

## Decision

Keep framework-specific translation at the package boundary and keep the
feedback domain independent of framework execution APIs.

## Consequences

- the domain model remains framework-independent
- upgrade impact is localized to framework boundaries
- testing can clearly separate unit tests from real integration tests
