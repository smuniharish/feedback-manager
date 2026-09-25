# Example: tool failure

A real LangChain `@tool` raises `TimeoutError`; `FeedbackCallbackHandler`
translates that into feedback automatically, without the application
needing to catch the exception itself.

Full source, embedded directly from `examples/03_tool_failure.py`:

```python title="examples/03_tool_failure.py"
--8<-- "examples/03_tool_failure.py"
```

## Real run

```console
$ uv run python examples/03_tool_failure.py
Feedback captured: source=tool category=timeout payload={'error': "weather service timed out looking up 'Canberra'", 'error_type': 'TimeoutError'}
Tool call failed -- application handles the exception as usual;
FeedbackManager has already recorded it independently.
Total feedback events recorded: 1
```

