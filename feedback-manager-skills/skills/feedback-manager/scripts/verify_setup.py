"""Check that feedback-manager is installed and works in this Python environment.

Runs offline and changes nothing. Prints one PASS, FAIL, or INFO line per check
and exits with status 1 if any check fails:

    python scripts/verify_setup.py
"""

from __future__ import annotations

import asyncio
import platform
import re
import sys
from importlib import metadata
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

REQUIRED = {
    "langgraph": ((1, 2, 12), (2,)),
    "langgraph-xai": ((1, 0, 0), (2,)),
    "langchain-core": ((1, 6, 6), (2,)),
    "pydantic": ((2, 13, 5), (3,)),
    "structlog": ((26, 1, 0), (27,)),
}

failures: list[str] = []


def report(passed: bool, message: str) -> bool:
    print(f"{'PASS' if passed else 'FAIL'}  {message}")
    if not passed:
        failures.append(message)
    return passed


def installed(distribution: str) -> str | None:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def release(version: str) -> tuple[int, ...]:
    """The numeric release of a version string: "1.2.12rc1" -> (1, 2, 12)."""
    numbers = []
    for part in version.split(".")[:3]:
        digits = re.match(r"\d+", part)
        numbers.append(int(digits.group()) if digits else 0)
    return tuple(numbers)


def dotted(version: tuple[int, ...]) -> str:
    return ".".join(map(str, version))


def check_environment() -> bool:
    report(
        sys.version_info >= (3, 12),
        f"Python {platform.python_version()} (3.12 or newer required)",
    )
    version = installed("feedback-manager")
    if version is None:
        return report(False, "feedback-manager is not installed: pip install feedback-manager")
    report(
        release(version)[:2] == (0, 1),
        f"feedback-manager {version} (0.1.x expected by this skill)",
    )
    for distribution, (lowest, below) in REQUIRED.items():
        found = installed(distribution)
        report(
            found is not None and lowest <= release(found) < below,
            f"{distribution} {found or 'is not installed'} "
            f"(>={dotted(lowest)},<{dotted(below)} required)",
        )
    return not failures


async def check_lifecycle() -> None:
    from feedback_manager import (
        ExecutionContext,
        FeedbackLifecycleError,
        FeedbackManager,
        FeedbackQuery,
        FeedbackStatus,
        FeedbackTarget,
    )
    from feedback_manager.observability import NoOpObservabilitySink

    manager = FeedbackManager(observability_sink=NoOpObservabilitySink())
    fields: dict[str, Any] = {
        "source": "human",
        "category": "correction",
        "target": FeedbackTarget(type="generation", id="gen-1"),
        "payload": {"corrected_text": "Canberra"},
        "execution_context": ExecutionContext(thread_id="verify-setup"),
        "idempotency_key": "verify-setup:correction",
    }
    feedback = await manager.submit(**fields)
    retried = await manager.submit(**fields)
    report(
        feedback.status is FeedbackStatus.RECEIVED
        and feedback.correlation_id == "verify-setup"
        and retried.feedback_id == feedback.feedback_id,
        "submit stores, correlates by thread, and deduplicates retries",
    )

    try:
        await manager.resolve(feedback.feedback_id)
        illegal_rejected = False
    except FeedbackLifecycleError:
        illegal_rejected = True
    await manager.acknowledge(feedback.feedback_id)
    await manager.mark_handled(feedback.feedback_id)
    resolved = await manager.resolve(feedback.feedback_id, resolution={"applied": True})
    stored = await manager.query(FeedbackQuery(status=FeedbackStatus.RESOLVED))
    report(
        illegal_rejected
        and resolved.resolution == {"applied": True}
        and [event.feedback_id for event in stored] == [feedback.feedback_id],
        "lifecycle rejects illegal moves and records the resolution",
    )


class Messages(TypedDict):
    messages: list[Any]


async def check_failure_capture() -> None:
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool
    from langgraph.graph import END, START, StateGraph
    from langgraph.prebuilt import ToolNode

    from feedback_manager import FeedbackManager
    from feedback_manager.integrations.langchain import FeedbackCallbackHandler
    from feedback_manager.observability import NoOpObservabilitySink

    @tool
    async def lookup(city: str) -> str:
        """Look up a city."""
        raise TimeoutError(f"lookup for {city!r} timed out")

    def plan(state: Messages) -> Messages:
        call = {"name": "lookup", "args": {"city": "Canberra"}, "id": "call-1"}
        return {"messages": [AIMessage(content="", tool_calls=[call])]}

    builder = StateGraph(Messages)
    builder.add_node("plan", plan)
    builder.add_node("tools", ToolNode([lookup], handle_tool_errors=False))
    builder.add_edge(START, "plan")
    builder.add_edge("plan", "tools")
    builder.add_edge("tools", END)

    manager = FeedbackManager(observability_sink=NoOpObservabilitySink())
    config: Any = {
        "callbacks": [FeedbackCallbackHandler(manager)],
        "configurable": {"thread_id": "verify-setup"},
    }
    try:
        await builder.compile().ainvoke({"messages": []}, config)
        propagated = False
    except TimeoutError:
        propagated = True
    events = await manager.query()
    report(
        propagated
        and len(events) == 1
        and (events[0].source, events[0].category) == ("tool", "timeout")
        and events[0].target.id == "call-1",
        "a tool timeout is recorded once and still propagates",
    )


class Approval(TypedDict, total=False):
    action: str
    decision: str


async def check_human_in_the_loop() -> None:
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    from feedback_manager import FeedbackManager, FeedbackStatus, FeedbackTarget
    from feedback_manager.integrations.langgraph import (
        HumanInTheLoopBridge,
        execution_context_from_snapshot,
        extract_interrupts,
    )
    from feedback_manager.observability import NoOpObservabilitySink

    def confirm(state: Approval) -> Approval:
        return {"decision": interrupt({"question": f"Run {state['action']}?"})}

    builder = StateGraph(Approval)
    builder.add_node("confirm", confirm)
    builder.add_edge(START, "confirm")
    builder.add_edge("confirm", END)
    graph = builder.compile(checkpointer=InMemorySaver())

    manager = FeedbackManager(observability_sink=NoOpObservabilitySink())
    bridge = HumanInTheLoopBridge(manager)
    config: Any = {"configurable": {"thread_id": "verify-setup"}}
    (pending,) = extract_interrupts(await graph.ainvoke({"action": "refund"}, config))
    request = await bridge.request(
        target=FeedbackTarget(type="node", id="confirm"),
        interrupt=pending,
        execution_context=execution_context_from_snapshot(await graph.aget_state(config)),
    )
    decision = await bridge.resolve(request.feedback_id, response="approved", approved=True)
    result = await graph.ainvoke(bridge.resume_command("approved"), config)
    context = request.execution_context
    report(
        context is not None
        and context.interrupt_id == pending.id
        and decision.status is FeedbackStatus.RESOLVED
        and result.get("decision") == "approved",
        "a human-in-the-loop request and decision are recorded around the interrupt",
    )


class Review(TypedDict, total=False):
    route: str


async def check_provenance() -> None:
    from langgraph.graph import END, START, StateGraph
    from langgraph_xai import DecisionType, XAIRuntime

    from feedback_manager import ExecutionContext, FeedbackManager, FeedbackTarget
    from feedback_manager.integrations.langgraph import execution_context_from_config
    from feedback_manager.observability import NoOpObservabilitySink

    xai = XAIRuntime(application_id="verify-setup", tenant_id="local", graph_id="review")
    manager = FeedbackManager(xai_runtime=xai, observability_sink=NoOpObservabilitySink())
    in_run: list[Any] = []

    async def route(state: Review, config: RunnableConfig) -> Review:
        await xai.record_decision(
            "REVIEW", decision_type=DecisionType.ROUTING, candidate_actions=["APPROVE", "REVIEW"]
        )
        feedback = await manager.submit(
            source="agent",
            category="request_for_human",
            target=FeedbackTarget(type="node", id="route"),
            execution_context=execution_context_from_config(config),
        )
        in_run.append(feedback.provenance)
        return {"route": "REVIEW"}

    builder = StateGraph(Review)
    builder.add_node("route", route)
    builder.add_edge(START, "route")
    builder.add_edge("route", END)
    graph = xai.instrument(builder.compile())
    with xai.collect_runs() as runs:
        await graph.ainvoke({})
    (run,) = runs
    later = await manager.submit(
        source="evaluator",
        category="quality",
        target=FeedbackTarget(type="run", id=str(run.run_id)),
        execution_context=ExecutionContext(run_id=str(run.run_id), node_id="route"),
    )
    (during,) = in_run
    report(
        during is not None
        and during.decision_id == str(run.decisions[0].id)
        and later.provenance is not None
        and later.provenance.node_execution_id is not None,
        "feedback links to langgraph-xai provenance during and after the run",
    )
    await xai.close()


def main() -> int:
    if check_environment():
        for check in (
            check_lifecycle,
            check_failure_capture,
            check_human_in_the_loop,
            check_provenance,
        ):
            try:
                asyncio.run(check())
            except Exception as error:  # report every failing check, then the summary
                report(False, f"{check.__name__}: {type(error).__name__}: {error}")
    print("All checks passed." if not failures else f"{len(failures)} check(s) failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
