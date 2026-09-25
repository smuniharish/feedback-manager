# Testing

The package validates three kinds of behavior:

- domain, lifecycle, routing, policy, and public-API behavior
- concurrency, idempotency, subscriber, and stream-isolation guarantees
- real LangChain, LangGraph, and `langgraph-xai` boundaries

The framework tests use actual dependency APIs rather than replacement
runtimes.

For contributors, run the full suite with:

```powershell
uv run pytest -q
```

Run with coverage:

```powershell
uv run coverage run -m pytest -q
uv run coverage report
```

See [Testing strategy](../architecture/TESTING_STRATEGY.md) for details.
