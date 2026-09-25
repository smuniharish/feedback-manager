# LangChain integration

Files:

- `integrations/langchain/callbacks.py`
- `integrations/langchain/tools.py`
- `integrations/langchain/adapter.py`

## Error callbacks

`FeedbackCallbackHandler` subclasses `langchain_core.callbacks.AsyncCallbackHandler` and translates runtime errors into feedback.

Covered callbacks:

- `on_tool_error()`
- `on_llm_error()`
- `on_chain_error()`
- `on_retriever_error()`

Category mapping:

- `TimeoutError` -> `timeout`
- `asyncio.CancelledError` -> `cancellation`
- everything else -> `failure`

## Example

```python
import asyncio

from langchain_core.tools import tool

from feedback_manager import FeedbackManager
from feedback_manager.integrations.langchain import FeedbackCallbackHandler


@tool
async def fetch_weather(city: str) -> str:
    raise TimeoutError(f"weather service timed out looking up {city!r}")


async def main() -> None:
    manager = FeedbackManager()
    handler = FeedbackCallbackHandler(manager)
    try:
        await fetch_weather.ainvoke({"city": "Canberra"}, config={"callbacks": [handler]})
    except TimeoutError:
        pass


asyncio.run(main())
```

That exact pattern is used in `examples/03_tool_failure.py` and the integration tests.

## Tool wrapper outside callback chains

For tool code not invoked through a LangChain runnable/callback chain, use `capture_tool_feedback()`:

```python
from feedback_manager.integrations.langchain import capture_tool_feedback

async with capture_tool_feedback(manager, tool_call_id="call-123"):
    await my_tool()
```
