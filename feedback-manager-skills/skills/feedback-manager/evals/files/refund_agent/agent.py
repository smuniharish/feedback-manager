"""A plain-Python refund agent with a tool call and a human approval step."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


class PaymentGatewayError(RuntimeError):
    pass


@dataclass(frozen=True)
class RefundRequest:
    run_id: str
    order_id: str
    amount: float


async def issue_refund(order_id: str, amount: float) -> str:
    if amount > 500:
        raise PaymentGatewayError("amount exceeds the gateway limit")
    return f"refund-{order_id}"


async def ask_supervisor(request: RefundRequest) -> bool:
    """Stand-in for a human approval UI."""
    return request.amount < 1000


async def run(request: RefundRequest) -> str:
    if request.amount > 200 and not await ask_supervisor(request):
        return "declined"
    try:
        return await issue_refund(request.order_id, request.amount)
    except PaymentGatewayError:
        return "failed"


if __name__ == "__main__":
    print(asyncio.run(run(RefundRequest("run-1", "A-17", 750.0))))
