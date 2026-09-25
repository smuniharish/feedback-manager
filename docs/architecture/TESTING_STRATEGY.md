# Testing Strategy

The package exercises actual dependencies rather than mocking framework
boundaries away.

## Current baseline

Verified before writing this document:

- `pytest`: **101 tests passed**
- `coverage report`: **97% total coverage**

## Test layers

### Domain and application-service tests

Focused tests for:

- core models and open string value types
- lifecycle rules
- in-memory store semantics
- routing behavior
- policy behavior
- error hierarchy
- correlation
- bundled handler and serializer
- `FeedbackManager` application service

### Concurrency tests

Concurrency-specific tests for:

- concurrent submissions
- idempotent dedup under concurrency
- concurrent lifecycle retries
- subscriber delivery under concurrent producers
- stream cancellation cleanup

### Framework-boundary tests

Real dependency integration tests for:

- LangChain callback/tool failure capture
- LangGraph interrupt/resume human-in-the-loop flows
- `langgraph-xai` provenance attachment and post-run resolution

## What is intentionally not done

- no fake replacement runtime for LangGraph
- no mock provenance model standing in for `langgraph-xai`
- no “pretend callbacks” standing in for LangChain core interfaces

The tests validate the real integration boundaries the package documents.

## Commands

The repository is configured to run tests with:

```powershell
uv run pytest -q
```

Coverage:

```powershell
uv run coverage run -m pytest -q
uv run coverage report
```
