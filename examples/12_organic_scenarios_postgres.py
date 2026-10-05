"""Populate PostgreSQL with realistic, varied feedback for the Grafana dashboard.

Each scenario is one `FeedbackManager.submit` call from a plausible situation in
an agent application, taken as far through the lifecycle as that situation
would go. Together they cover every well-known category and target type, so
the dashboard of ``examples/11_grafana_dashboard.py`` shows varied usage.

Run with:

    FEEDBACK_MANAGER_POSTGRES_DSN=postgresql://feedback:feedback@localhost:5432/feedback \\
        uv run python examples/12_organic_scenarios_postgres.py
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Any

from postgres_feedback_store import DSN_VARIABLE, PostgresFeedbackStore, run

from feedback_manager import FeedbackCategory, FeedbackManager, FeedbackTarget, FeedbackTargetType


@dataclass(frozen=True)
class Scenario:
    source: str
    category: str
    target_type: str
    target_id: str
    payload: dict[str, Any]
    steps: tuple[str, ...] = ()
    resolution: dict[str, Any] = field(default_factory=dict)


SCENARIOS = [
    Scenario(
        "agent",
        "approval",
        "tool_call",
        "delete-table-call-1",
        {"reason": "destructive tool call needs sign-off"},
        ("acknowledge", "reject"),
        {"reason": "not approved by the reviewer"},
    ),
    Scenario(
        "human",
        "rejection",
        "tool_call",
        "delete-table-call-1",
        {"reason": "too risky"},
        ("acknowledge", "mark_handled", "resolve"),
        {"applied": True},
    ),
    Scenario(
        "tool",
        "timeout",
        "tool_result",
        "fetch-weather-call-1",
        {"timeout_seconds": 30},
        ("acknowledge", "mark_handled", "resolve"),
        {"retried": True},
    ),
    Scenario(
        "tool",
        "failure",
        "tool_call",
        "fetch-weather-call-2",
        {"error": "service unavailable after retry"},
        ("acknowledge",),
    ),
    Scenario(
        "evaluator", "uncertainty", "message", "msg-9021", {"confidence": 0.31}, ("acknowledge",)
    ),
    Scenario(
        "evaluator",
        "validation",
        "state",
        "thread-7-state",
        {"schema_errors": ["missing customer_id"]},
        ("acknowledge", "mark_handled", "resolve"),
        {"schema_fixed": True},
    ),
    Scenario(
        "agent",
        "request_for_human",
        "run",
        "run-1102",
        {"reason": "ambiguous customer intent"},
        ("acknowledge",),
    ),
    Scenario(
        "human",
        "cancellation",
        "thread",
        "support-4471",
        {"reason": "customer ended the chat"},
        ("cancel",),
        {"reason": "conversation closed"},
    ),
    Scenario(
        "system",
        "partial_result",
        "task",
        "summarize-report",
        {"completed_sections": 3, "total_sections": 5},
        ("acknowledge",),
    ),
    Scenario(
        "system",
        "comment",
        "checkpoint",
        "checkpoint-88f2",
        {"note": "replayed after a deploy rollback"},
    ),
    Scenario(
        "application", "comment", "application", "support-bot", {"note": "nightly health check"}
    ),
    Scenario(
        "human",
        "correction",
        "message",
        "msg-9022",
        {"corrected": "Refunds take 3-5 business days."},
        ("acknowledge", "mark_handled", "resolve"),
        {"applied": True},
    ),
    Scenario("external", "rating", "agent", "support-agent-v3", {"rating": 5, "channel": "survey"}),
    Scenario("system", "completion", "graph", "support-graph", {"outcome": "resolved"}),
    Scenario(
        "human",
        "approval",
        "node",
        "send-refund",
        {"approved": True},
        ("acknowledge", "mark_handled", "resolve"),
        {"applied": True},
    ),
    Scenario(
        "generation",
        "interruption",
        "generation",
        "gen-9901",
        {"reason": "client disconnected mid-stream"},
        ("acknowledge",),
    ),
    Scenario("evaluator", "quality", "generation", "gen-9902", {"score": 0.92}, ("expire",)),
]


async def main() -> None:
    dsn = os.environ.get(DSN_VARIABLE)
    if not dsn:
        sys.exit(f"Set {DSN_VARIABLE} to the PostgreSQL connection string first.")
    async with await PostgresFeedbackStore.open(dsn) as store:
        manager = FeedbackManager(store=store)
        for scenario in SCENARIOS:
            feedback = await manager.submit(
                source=scenario.source,
                category=scenario.category,
                target=FeedbackTarget(type=scenario.target_type, id=scenario.target_id),
                payload=scenario.payload,
            )
            for step in scenario.steps:
                closing = step in {"resolve", "reject", "cancel"}
                arguments = {"resolution": scenario.resolution} if closing else {}
                feedback = await getattr(manager, step)(feedback.feedback_id, **arguments)
            print(
                f"{feedback.source:<11} {feedback.category:<18} "
                f"{feedback.target.type:<12} -> {feedback.status}"
            )

    categories = {scenario.category for scenario in SCENARIOS}
    target_types = {scenario.target_type for scenario in SCENARIOS}
    print(f"Categories covered: {len(categories)} of {len(FeedbackCategory.known_values())}")
    print(f"Target types covered: {len(target_types)} of {len(FeedbackTargetType.known_values())}")


if __name__ == "__main__":
    run(main())
