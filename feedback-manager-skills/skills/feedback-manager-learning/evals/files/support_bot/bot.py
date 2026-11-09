"""A support bot that records reviewer corrections but never learns from them."""

from __future__ import annotations

import asyncio

from feedback_manager import ExecutionContext, FeedbackManager, FeedbackTarget, FeedbackTargetType

manager = FeedbackManager()


def build_prompt(question: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "You are a billing support assistant."},
        {"role": "user", "content": question},
    ]


async def record_correction(run_id: str, wrong: str, right: str) -> None:
    await manager.submit(
        source="human",
        category="correction",
        target=FeedbackTarget(type=FeedbackTargetType.RUN, id=run_id),
        payload={"incorrect": wrong, "correction": right},
        execution_context=ExecutionContext(tenant_id="acme", agent_id="billing-bot", run_id=run_id),
    )


async def main() -> None:
    await record_correction("run-1", "Refunds take 30 days.", "Refunds take 5 business days.")
    print(build_prompt("How long does a refund take?"))
    await manager.aclose()


if __name__ == "__main__":
    asyncio.run(main())
