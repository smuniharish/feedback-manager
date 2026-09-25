"""Example 3 -- Tool failure and timeout feedback.

A LangChain tool times out; a LangChain callback handler translates that
into feedback automatically, without the application needing to catch the
exception itself.

Run with::

    uv run python examples/03_tool_failure.py
"""

import asyncio
import os
import selectors
import sys

from langchain_core.tools import tool

from feedback_manager import FeedbackEvent, FeedbackManager
from feedback_manager.integrations.langchain import FeedbackCallbackHandler


@tool
async def fetch_weather(city: str) -> str:
    """Look up the weather for a city (simulated to always time out)."""
    raise TimeoutError(f"weather service timed out looking up {city!r}")


async def _build_manager() -> FeedbackManager:
    # Zero-config by default (in-memory store); set FEEDBACK_MANAGER_POSTGRES_DSN
    # to run this exact scenario against the real PostgreSQL store instead --
    # see docs/examples/grafana-observability.md.
    dsn = os.environ.get("FEEDBACK_MANAGER_POSTGRES_DSN")
    if not dsn:
        return FeedbackManager()
    from postgres_feedback_store import PostgresFeedbackStore

    store = await PostgresFeedbackStore.connect(dsn)
    return FeedbackManager(store=store)


def _run(coro):
    # psycopg's async mode needs a selector event loop; Windows defaults to
    # the proactor loop, so only override it there.
    if sys.platform == "win32":
        return asyncio.run(
            coro, loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        )
    return asyncio.run(coro)


async def main() -> None:
    manager = await _build_manager()

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
    _run(main())
