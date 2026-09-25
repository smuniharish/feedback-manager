"""Example 12 -- genuinely diverse feedback, captured for real into Postgres.

Examples 1-9 each demonstrate *one* realistic (source, category, target
type) combination using the real, default in-memory store, so every one of
them can be run standalone with zero setup. None of them write to the real
PostgreSQL store used by the Streamlit UI and the Grafana dashboard, so
that store only ever saw two organic categories (``rating``, ``quality``)
and one organic target type (``generation``) -- everything else in the
dashboard's "real / organic feedback" row was legitimately zero, and the
only reason ``events by category``/``events by target type`` looked
non-zero for *every* value was ``examples/10_full_matrix_feedback.py``'s
synthetic combinatorial probes (now excluded from that row -- see
``examples/11_grafana_dashboard.py``).

This script closes that gap for real: it exercises a further set of
plausible, distinct application scenarios -- an agent requesting approval
before a risky tool call, a tool timing out, an evaluator flagging low
confidence, a human cancelling a long-running thread, a checkpoint replay
audit note, and so on -- each a genuine ``FeedbackManager.submit()`` call
(several taken through ``acknowledge`` -> ``mark_handled`` -> ``resolve``)
against the real ``PostgresFeedbackStore``, so the Grafana dashboard's
organic panels reflect real, varied usage instead of a single narrow path.

Run with (``FEEDBACK_MANAGER_POSTGRES_DSN`` must point at the real
PostgreSQL instance -- see ``docs/examples/grafana-observability.md``)::

    uv run python examples/12_organic_scenarios_postgres.py
"""

from __future__ import annotations

import asyncio
import os
import selectors
import sys

from feedback_manager import (
    FeedbackCategory,
    FeedbackManager,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)


def _run(coro):
    if sys.platform == "win32":
        return asyncio.run(
            coro, loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        )
    return asyncio.run(coro)


async def _build_manager() -> FeedbackManager:
    dsn = os.environ.get("FEEDBACK_MANAGER_POSTGRES_DSN")
    if not dsn:
        raise SystemExit(
            "Set FEEDBACK_MANAGER_POSTGRES_DSN to the real PostgreSQL instance first -- "
            "this example exists specifically to populate the real store."
        )
    from postgres_feedback_store import PostgresFeedbackStore

    store = await PostgresFeedbackStore.connect(dsn)
    return FeedbackManager(store=store)


async def main() -> None:
    manager = await _build_manager()

    # 1. Agent asks a human to approve a risky tool call, then the human
    #    rejects it -- two real categories, one real target type.
    approval = await manager.submit(
        source=FeedbackSource.AGENT,
        category=FeedbackCategory.APPROVAL,
        target=FeedbackTarget(type=FeedbackTargetType.TOOL_CALL, id="delete_production_table"),
        payload={"reason": "destructive tool call requires sign-off"},
    )
    await manager.acknowledge(approval.feedback_id)
    await manager.mark_handled(approval.feedback_id)
    await manager.reject(approval.feedback_id, reason="not approved by human reviewer")
    rejection = await manager.submit(
        source=FeedbackSource.HUMAN,
        category=FeedbackCategory.REJECTION,
        target=FeedbackTarget(type=FeedbackTargetType.TOOL_CALL, id="delete_production_table"),
        payload={"reason": "too risky, not approved"},
        metadata={"related_feedback_id": str(approval.feedback_id)},
    )
    await manager.acknowledge(rejection.feedback_id)
    await manager.mark_handled(rejection.feedback_id)
    await manager.resolve(rejection.feedback_id, resolution={"applied": True})

    # 2. A tool times out, then genuinely fails on retry.
    timeout_event = await manager.submit(
        source=FeedbackSource.TOOL,
        category=FeedbackCategory.TIMEOUT,
        target=FeedbackTarget(type=FeedbackTargetType.TOOL_RESULT, id="fetch_weather-call-1"),
        payload={"timeout_seconds": 30},
    )
    await manager.acknowledge(timeout_event.feedback_id)
    await manager.mark_handled(timeout_event.feedback_id)
    await manager.resolve(timeout_event.feedback_id, resolution={"retried": True})
    failure = await manager.submit(
        source=FeedbackSource.TOOL,
        category=FeedbackCategory.FAILURE,
        target=FeedbackTarget(type=FeedbackTargetType.TOOL_CALL, id="fetch_weather-call-2"),
        payload={"error": "weather service unavailable after retry"},
    )
    await manager.acknowledge(failure.feedback_id)

    # 3. An evaluator flags low confidence in a message, and a validation
    #    failure against the agent's own state.
    uncertainty = await manager.submit(
        source=FeedbackSource.EVALUATOR,
        category=FeedbackCategory.UNCERTAINTY,
        target=FeedbackTarget(type=FeedbackTargetType.MESSAGE, id="msg-9021"),
        payload={"confidence": 0.31},
    )
    await manager.acknowledge(uncertainty.feedback_id)
    validation = await manager.submit(
        source=FeedbackSource.EVALUATOR,
        category=FeedbackCategory.VALIDATION,
        target=FeedbackTarget(type=FeedbackTargetType.STATE, id="agent-state-thread-7"),
        payload={"schema_errors": ["missing required field 'customer_id'"]},
    )
    await manager.acknowledge(validation.feedback_id)
    await manager.mark_handled(validation.feedback_id)
    await manager.resolve(validation.feedback_id, resolution={"schema_fixed": True})

    # 4. An agent requests human intervention mid-run; a human cancels a
    #    long-running thread; a system reports a partial result on a task.
    request_for_human = await manager.submit(
        source=FeedbackSource.AGENT,
        category=FeedbackCategory.REQUEST_FOR_HUMAN,
        target=FeedbackTarget(type=FeedbackTargetType.RUN, id="run-2024-1102"),
        payload={"reason": "ambiguous customer intent, needs disambiguation"},
    )
    await manager.acknowledge(request_for_human.feedback_id)
    cancellation = await manager.submit(
        source=FeedbackSource.HUMAN,
        category=FeedbackCategory.CANCELLATION,
        target=FeedbackTarget(type=FeedbackTargetType.THREAD, id="thread-support-4471"),
        payload={"reason": "customer ended the chat"},
    )
    await manager.acknowledge(cancellation.feedback_id)
    await manager.mark_handled(cancellation.feedback_id)
    await manager.resolve(cancellation.feedback_id, resolution={"cleanup": "done"})
    partial_result = await manager.submit(
        source=FeedbackSource.SYSTEM,
        category=FeedbackCategory.PARTIAL_RESULT,
        target=FeedbackTarget(type=FeedbackTargetType.TASK, id="task-summarize-report"),
        payload={"completed_sections": 3, "total_sections": 5},
    )
    await manager.acknowledge(partial_result.feedback_id)

    # 5. A checkpoint replay audit note, an application health comment, a
    #    correction on a message, and an external rating of the agent.
    checkpoint_comment = await manager.submit(
        source=FeedbackSource.SYSTEM,
        category=FeedbackCategory.COMMENT,
        target=FeedbackTarget(type=FeedbackTargetType.CHECKPOINT, id="checkpoint-88f2"),
        payload={"note": "replayed from checkpoint after deploy rollback"},
    )
    application_comment = await manager.submit(
        source=FeedbackSource.APPLICATION,
        category=FeedbackCategory.COMMENT,
        target=FeedbackTarget(type=FeedbackTargetType.APPLICATION, id="support-bot-prod"),
        payload={"note": "nightly health check"},
    )
    message_correction = await manager.submit(
        source=FeedbackSource.HUMAN,
        category=FeedbackCategory.CORRECTION,
        target=FeedbackTarget(type=FeedbackTargetType.MESSAGE, id="msg-9022"),
        payload={"corrected_text": "Refunds take 3-5 business days, not 24 hours."},
    )
    await manager.acknowledge(message_correction.feedback_id)
    await manager.mark_handled(message_correction.feedback_id)
    await manager.resolve(message_correction.feedback_id, resolution={"applied": True})
    agent_rating = await manager.submit(
        source=FeedbackSource.EXTERNAL,
        category=FeedbackCategory.RATING,
        target=FeedbackTarget(type=FeedbackTargetType.AGENT, id="support-agent-v3"),
        payload={"rating": 5, "channel": "CSAT survey"},
    )

    # 6. A completion note on the graph itself and a node-level approval,
    #    rounding out target-type coverage.
    graph_completion = await manager.submit(
        source=FeedbackSource.SYSTEM,
        category=FeedbackCategory.COMPLETION,
        target=FeedbackTarget(type=FeedbackTargetType.GRAPH, id="support-graph-v3"),
        payload={"run_id": "run-2024-1102", "outcome": "resolved"},
    )
    node_approval = await manager.submit(
        source=FeedbackSource.HUMAN,
        category=FeedbackCategory.APPROVAL,
        target=FeedbackTarget(type=FeedbackTargetType.NODE, id="send_refund_node"),
        payload={"approved": True},
    )
    await manager.acknowledge(node_approval.feedback_id)
    await manager.mark_handled(node_approval.feedback_id)
    await manager.resolve(node_approval.feedback_id, resolution={"applied": True})

    # 7. A long-running generation is interrupted mid-stream (e.g. the user
    #    navigated away) -- the one category the earlier scenarios missed.
    interruption = await manager.submit(
        source=FeedbackSource.GENERATION,
        category=FeedbackCategory.INTERRUPTION,
        feedback_type="generation_interrupted",
        target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id="gen-support-9901"),
        payload={"reason": "client disconnected mid-stream"},
    )
    await manager.acknowledge(interruption.feedback_id)

    submitted = [
        approval,
        rejection,
        timeout_event,
        failure,
        uncertainty,
        validation,
        request_for_human,
        cancellation,
        partial_result,
        checkpoint_comment,
        application_comment,
        message_correction,
        agent_rating,
        graph_completion,
        node_approval,
        interruption,
    ]
    print(f"Submitted {len(submitted)} genuinely distinct real feedback events to PostgreSQL:")
    for event in submitted:
        print(
            f"  - source={event.source:<11} category={event.category:<18} "
            f"target_type={event.target.type:<11} target_id={event.target.id}"
        )

    distinct_categories = {str(e.category) for e in submitted}
    distinct_target_types = {str(e.target.type) for e in submitted}
    print(f"\nDistinct categories captured this run: {len(distinct_categories)}")
    print(f"Distinct target types captured this run: {len(distinct_target_types)}")


if __name__ == "__main__":
    _run(main())
