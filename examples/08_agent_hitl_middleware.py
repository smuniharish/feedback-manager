"""Record a human approval for a tool call made by a `create_agent` agent.

`HumanInTheLoopMiddleware` pauses the agent before `send_refund` runs. The
application shows the pending call to a reviewer, and
`LangGraphHumanInTheLoopBridge` keeps a feedback record of the request and the
decision. The model is a scripted fake so the example runs offline; in an
application, pass `init_chat_model(...)` instead.

Run with:

    uv run python examples/08_agent_hitl_middleware.py
"""

import asyncio
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from feedback_manager import FeedbackManager, FeedbackTarget, FeedbackTargetType
from feedback_manager.integrations.frameworks.langchain import (
    LangGraphHumanInTheLoopBridge,
    execution_context_from_snapshot,
    extract_interrupts,
)


@tool
def send_refund(order_id: str) -> str:
    """Send the refund for an order."""
    return f"Refund sent for order {order_id}"


class ScriptedModel(GenericFakeChatModel):
    """Replays scripted replies; it ignores the tool schemas that `create_agent` binds."""

    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any) -> Any:
        return self


def build_agent() -> Any:
    model = ScriptedModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "send_refund", "args": {"order_id": "1042"}, "id": "call-refund-1"}
                    ],
                ),
                AIMessage(content="The refund for order 1042 was sent."),
            ]
        )
    )
    return create_agent(
        model=model,
        tools=[send_refund],
        middleware=[
            HumanInTheLoopMiddleware(
                interrupt_on={"send_refund": {"allowed_decisions": ["approve", "reject"]}}
            )
        ],
        checkpointer=InMemorySaver(),
    )


async def main() -> None:
    manager = FeedbackManager()
    bridge = LangGraphHumanInTheLoopBridge(manager)
    agent = build_agent()
    config: RunnableConfig = {"configurable": {"thread_id": "refund-1042"}}

    paused = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "Refund order 1042"}]}, config
    )
    (pending,) = extract_interrupts(paused)
    snapshot = await agent.aget_state(config)

    request = await bridge.request(
        target=FeedbackTarget(type=FeedbackTargetType.TOOL_CALL, id="call-refund-1"),
        interrupt=pending,
        execution_context=execution_context_from_snapshot(snapshot),
    )
    print(f"Agent paused; approval request {request.feedback_id} is {request.status}")

    # The reviewer approves in your application's UI.
    decision = await bridge.resolve(request.feedback_id, response="approve", approved=True)
    print(f"Decision recorded: {decision.status}, resolution={decision.resolution}")

    result = await agent.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
    tool_result = next(
        message for message in result["messages"] if isinstance(message, ToolMessage)
    )
    print(f"Tool ran after approval: {tool_result.text}")
    print(f"Agent resumed and finished: {result['messages'][-1].text}")


if __name__ == "__main__":
    asyncio.run(main())
