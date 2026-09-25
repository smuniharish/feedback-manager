# ADR 0004: Exclude self-improvement and agent runtime scope

## Context

Feedback systems often drift into owning evaluation loops, automated self-improvement, or full runtime orchestration.

## Decision

Keep `feedback-manager` scoped to feedback capture, correlation, persistence, routing, and lifecycle only.

## Consequences

- the package does not become an agent framework
- business workflows remain in application code
- LangGraph and LangChain continue to own execution semantics
- feedback records remain reusable across many higher-level workflows

