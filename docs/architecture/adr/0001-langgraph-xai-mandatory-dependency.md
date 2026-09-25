# ADR 0001: `langgraph-xai` as a mandatory dependency

## Context

Provenance is a first-class package capability and is backed by the
application's real `XAIRuntime`.

## Decision

Keep `langgraph-xai` as a mandatory runtime dependency instead of an optional extra.

## Consequences

- provenance can be documented and tested as a first-class capability
- no provider-selection or optional-dependency branches are needed
- installation is simpler for users in the intended ecosystem
- the package is intentionally opinionated about LangGraph explainability support
