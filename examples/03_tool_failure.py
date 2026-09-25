"""Example 3 -- Tool failure and timeout feedback.

A LangChain tool times out; a LangChain callback handler translates that
into feedback automatically, without the application needing to catch the
exception itself.

Run with::

    uv run python examples/03_tool_failure.py
"""

import asyncio

from langchain_core.tools import tool

from feedback_manager import FeedbackEvent, FeedbackManager
from feedback_manager.integrations.langchain import FeedbackCallbackHandler


@tool
async def fetch_weather(city: str) -> str:
    """Look up the weather for a city (simulated to always time out)."""
    raise TimeoutError(f"weather service timed out looking up {city!r}")


async def main() -> None:
    manager = FeedbackManager()

    async def print_feedback(feedback: FeedbackEvent) -> None:
        print(
            f"Feedback captured: source={feedback.source} category={feedback.category} "
            f"payload={feedback.payload}"
        )

    manager.subscribe(print_feedback)
    handler = FeedbackCallbackHandler(manager)

    try:
        await fetch_weather.ainvoke({"city": "Canberra"}, config={"callbacks": [handler]})
    except TimeoutError:
        print("Tool call failed -- application handles the exception as usual;")
        print("FeedbackManager has already recorded it independently.")

    events = await manager.list()
    print(f"Total feedback events recorded: {len(events)}")


if __name__ == "__main__":
    asyncio.run(main())
