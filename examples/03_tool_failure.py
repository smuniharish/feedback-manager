"""Turn a tool timeout inside a LangGraph agent into one feedback event, automatically.

`FeedbackCallbackHandler` uses LangChain's callback system: the application
keeps handling the exception as usual, and the failure is recorded once, with
the thread, node, and tool call it happened in.

Run with:

    uv run python examples/03_tool_failure.py
"""

import asyncio
from typing import Any, TypedDict

from langchain_core.messages import AIMessage, AnyMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from feedback_manager import FeedbackManager
from feedback_manager.integrations.langchain import FeedbackCallbackHandler


class State(TypedDict):
    messages: list[AnyMessage]


@tool
async def fetch_weather(city: str) -> str:
    """Look up the weather for a city."""
    raise TimeoutError(f"weather service did not answer for {city!r} within 5 seconds")


def plan(state: State) -> State:
    """Stands in for a model call that decides to use the weather tool."""
    call = {"name": "fetch_weather", "args": {"city": "Canberra"}, "id": "call-weather-1"}
    return {"messages": [AIMessage(content="", tool_calls=[call])]}


def build_graph() -> Any:
    builder = StateGraph(State)
    builder.add_node("plan", plan)
    builder.add_node("tools", ToolNode([fetch_weather], handle_tool_errors=False))
    builder.add_edge(START, "plan")
    builder.add_edge("plan", "tools")
    builder.add_edge("tools", END)
    return builder.compile()


async def main() -> None:
    manager = FeedbackManager()
    config: RunnableConfig = {
        "callbacks": [FeedbackCallbackHandler(manager)],
        "configurable": {"thread_id": "weather-chat-3"},
    }

    try:
        await build_graph().ainvoke({"messages": []}, config)
    except TimeoutError as error:
        print(f"The agent failed as usual: {error}")

    for event in await manager.query():
        context = event.execution_context
        assert context is not None
        print(
            f"Recorded {event.source}/{event.category} feedback about "
            f"{event.target.type} {event.target.id!r} "
            f"(thread={context.thread_id}, node={context.node_id})"
        )
        print(f"  payload: {event.payload}")


if __name__ == "__main__":
    asyncio.run(main())
