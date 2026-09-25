# Architecture

`feedback-manager` is a **library** that gives LangChain and LangGraph applications a durable, typed feedback domain. It captures feedback events, correlates them to execution context, persists them, routes them to handlers, and manages their lifecycle. It does **not** run agents, schedule graphs, own interrupts, own checkpoints, or replace framework runtimes.

## Why a library instead of a runtime?

The codebase deliberately keeps execution ownership outside this package:

- LangGraph still owns graph execution, checkpointing, interrupts, and streaming.
- LangChain still owns callback dispatch, tool invocation, and runnable composition.
- `langgraph-xai` still owns provenance capture and storage.
- application code still owns business workflows, UI, access control, and external side effects.

`FeedbackManager` sits beside those systems as an application service. Its job is to make feedback a first-class concern without becoming another runtime layer.

## Layering

The implementation follows a small layered design.

### 1. Public API

Primary entry points:

- `feedback_manager.FeedbackManager`
- `feedback_manager.FeedbackQuery`
- core value/model types such as `FeedbackEvent`, `FeedbackSource`, `FeedbackCategory`, and `FeedbackTarget`

This stable root surface is intentionally small.

### 2. Application service

`FeedbackManager` is the orchestration point for the feedback pipeline:

1. build a `FeedbackEvent`
2. optionally apply a redaction policy
3. derive correlation
4. optionally resolve provenance
5. persist the event
6. transition it to `RECEIVED`
7. notify subscribers and stream consumers
8. route to handlers

This service wires together contracts through dependency injection. There is no hidden global singleton.

### 3. Domain model

The framework-independent domain consists of:

- `FeedbackEvent`
- `FeedbackSource`
- `FeedbackCategory`
- `FeedbackTarget` and `FeedbackTargetType`
- `ExecutionContext`
- `CorrelationContext`
- `FeedbackProvenanceReference`
- `FeedbackStatus`
- explicit lifecycle transition rules

The core layer never imports LangChain, LangGraph, or `langgraph-xai`.

### 4. Contracts

Documented extension contracts provide:

- ABCs for stateful components with behavioral invariants, such as `FeedbackStore`, `FeedbackHandler`, `FeedbackRouter`, and the policy hooks
- Protocols for structural contracts such as `FeedbackSubscriber`, `FeedbackSerializer`, and `FeedbackCorrelator`

Provenance is deliberately **not** one of these generic contracts:
`langgraph-xai` is a mandatory runtime dependency and the sole supported
provenance source (see [Provenance model](PROVENANCE_MODEL.md)).

This keeps the manager stable while making infrastructure replaceable.

### 5. Defaults and framework boundaries

The package provides:

- `InMemoryFeedbackStore` for local/reference persistence
- `DefaultFeedbackRouter` and `RoutingRule`
- `DefaultFeedbackCorrelator`
- `AuditFeedbackHandler`
- failure, delivery, and retention policies
- observability hooks and a logging sink
- documented helpers for LangChain callbacks and LangGraph human-in-the-loop flows
- automatic provenance correlation through the supplied `XAIRuntime`

## Pipeline ownership

The feedback pipeline is deliberately narrower than an agent runtime:

- **capture**: `submit()`
- **correlate**: `FeedbackCorrelator`
- **persist**: `FeedbackStore`
- **route**: `FeedbackRouter` and `FeedbackHandler`
- **resolve**: lifecycle transitions on stored events

Everything else is delegated to upstream frameworks or application code.

## Default components

Calling `FeedbackManager()` with no arguments gives a usable default setup:

- store: `InMemoryFeedbackStore`
- router: `DefaultFeedbackRouter`
- correlator: `DefaultFeedbackCorrelator`
- failure policy: `FailurePolicy`
- observability sink: `LoggingObservabilitySink`

These defaults are intentionally modest. They make local use, tests, and examples easy, but production applications are expected to swap in their own persistence, routing, and policy components.

## No global runtime state

Each `FeedbackManager` instance owns its own:

- store dependency
- router dependency
- correlator dependency
- subscriber list
- stream queues
- policies and observability sink

Multiple manager instances are isolated by design.

## Thin framework boundaries

Framework-specific behavior is kept at the edge:

- LangChain helpers translate callback errors and tool failures into feedback events
- LangGraph helpers extract execution identifiers and record decisions around
  native `interrupt()` / `Command(resume=...)` flows
- the mandatory `langgraph-xai` boundary translates execution provenance into
  `FeedbackProvenanceReference`

This keeps the core domain stable even if framework details evolve.

## Practical mental model

Think of the package as:

> a typed feedback ledger plus routing/lifecycle helpers for LangChain/LangGraph applications

Not as:

> an agent framework, scheduler, evaluator engine, provenance engine, or orchestration platform
