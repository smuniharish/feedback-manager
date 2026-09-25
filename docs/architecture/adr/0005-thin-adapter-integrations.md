# ADR 0005: Keep integrations thin and localized

## Context

Framework-specific code changes faster than the core feedback domain.

## Decision

Place framework-specific logic under `integrations/` and keep the core layer free of direct imports from LangChain, LangGraph, and `langgraph-xai`.

## Consequences

- the domain model remains framework-independent
- upgrade impact is localized to adapters
- testing can clearly separate unit tests from real integration tests

