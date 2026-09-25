# Testing Strategy

The package ships a real test suite under `tests/` and exercises actual dependencies rather than mocking the frameworks away.

## Current baseline

Verified before writing this document:

- `pytest`: **100 tests passed**
- `coverage report`: **97% total coverage**

## Test layout

### `tests/unit/`

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

### `tests/concurrency/`

Concurrency-specific tests for:

- concurrent submissions
- idempotent dedup under concurrency
- concurrent lifecycle retries
- subscriber delivery under concurrent producers
- stream cancellation cleanup

### `tests/integration/`

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
uv run pytest tests -q
```

Coverage:

```powershell
.venv\Scripts\python.exe -m coverage run -m pytest tests -q
.venv\Scripts\python.exe -m coverage report
```

