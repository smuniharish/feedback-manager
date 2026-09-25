# LangChain callbacks and tools

Use these LangChain helpers when feedback should be captured automatically
from callback failures or around a tool invocation.

The supported public imports are:

```python
from feedback_manager.integrations.langchain import (
    FeedbackCallbackHandler,
    capture_tool_feedback,
)
```

## Capture callback failures

Create one `FeedbackCallbackHandler` for your manager and pass it through
LangChain's normal `callbacks` configuration. LangChain continues to own
execution and callback dispatch; the handler only records failures as
feedback.

The handler captures failures reported by tool, model, chain, and retriever
callbacks. It classifies timeouts as `timeout`, cancellations as
`cancellation`, and other exceptions as `failure`.

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

The exception still propagates normally. Feedback capture does not replace
your application's error handling.

See [Tool failure](../examples/tool-failure.md) for the complete runnable
example and verified output.

## Capture failures outside callback chains

For tool code that is not invoked through a LangChain callback-enabled
runnable, wrap the call with `capture_tool_feedback()`:

```python
from feedback_manager.integrations.langchain import capture_tool_feedback

async with capture_tool_feedback(manager, tool_call_id="call-123"):
    await my_tool()
```

This wrapper records the failure and re-raises the original exception.
