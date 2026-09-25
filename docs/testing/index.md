# Testing

Run the full suite:

```powershell
uv run pytest tests -q
```

Run with coverage:

```powershell
.venv\Scripts\python.exe -m coverage run -m pytest tests -q
.venv\Scripts\python.exe -m coverage report
```

Layout:

- `tests/unit/`
- `tests/concurrency/`
- `tests/integration/`

See [Testing strategy](../architecture/TESTING_STRATEGY.md) for details.

