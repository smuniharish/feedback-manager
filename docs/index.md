# feedback-manager

`feedback-manager` is a production-grade Python library for treating feedback as a first-class domain concern in LangChain and LangGraph applications.

It gives you:

- a typed feedback event model
- lifecycle management (`RECEIVED -> ACKNOWLEDGED -> HANDLED -> RESOLVED`)
- correlation to runs, threads, checkpoints, nodes, tool calls, and generations
- pluggable persistence, routing, handlers, policies, and observability
- framework helpers for LangChain callbacks and LangGraph human-in-the-loop
  flows
- provenance correlation backed exclusively by the mandatory
  `langgraph-xai` runtime

It does **not** give you:

- an agent runtime
- graph orchestration
- an evaluator framework
- a self-improvement engine
- a replacement for LangGraph interrupts, checkpoints, or streaming

Use it when you need to capture human corrections, approvals, tool failures, evaluator scores, interruptions, and provenance-linked feedback around an existing LangChain/LangGraph system.

## Why it exists

Agent applications often accumulate feedback in ad hoc ways:

- comments in a UI but no durable model
- tool failures in logs but not queryable by run
- approvals in LangGraph interrupts but not persisted as domain records
- evaluator scores disconnected from the generation they refer to

`feedback-manager` centralizes those concerns without taking over runtime ownership from the frameworks that already do it well.

## Start here

- [Installation](getting-started/installation.md)
- [Quickstart](getting-started/quickstart.md)
- [Core concepts](concepts/sources.md)
- [Architecture](architecture/ARCHITECTURE.md)
- [Agent Skills](agent-skills.md)
