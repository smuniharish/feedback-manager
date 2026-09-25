# ADR 0001: `langgraph-xai` as a mandatory dependency

## Context

The package exposes real provenance integration through `XAIProvenanceAdapter`. That implementation imports and uses `XAIRuntime`, `Execution`, and `ProvenanceStore` directly.

## Decision

Keep `langgraph-xai` as a mandatory runtime dependency instead of an optional extra.

## Consequences

- provenance can be documented and tested as a first-class capability
- no conditional import branches are needed for the bundled adapter
- installation is simpler for users in the intended ecosystem
- the package is intentionally opinionated about LangGraph explainability support

