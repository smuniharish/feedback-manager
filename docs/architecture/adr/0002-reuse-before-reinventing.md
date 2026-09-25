# ADR 0002: Reuse framework mechanisms before inventing new ones

## Context

LangChain, LangGraph, and `langgraph-xai` already provide callback, interrupt, streaming, and provenance mechanisms.

## Decision

Implement thin adapters that reuse those mechanisms instead of replacing them.

## Consequences

- `FeedbackCallbackHandler` uses LangChain callbacks directly
- `HumanInTheLoopBridge` wraps LangGraph `interrupt()` and `Command(resume=...)`
- `XAIProvenanceAdapter` reads from `XAIRuntime` and `ProvenanceStore`
- core package complexity stays lower and duplication is avoided

