# Example: tool failure

Source file: `examples/03_tool_failure.py`

This example shows a real LangChain `@tool` raising `TimeoutError` and being captured automatically by `FeedbackCallbackHandler`.

Key pattern:

```python
handler = FeedbackCallbackHandler(manager)

try:
    await fetch_weather.ainvoke({"city": "Canberra"}, config={"callbacks": [handler]})
except TimeoutError:
    ...
```

