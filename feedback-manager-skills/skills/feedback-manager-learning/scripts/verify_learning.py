"""Check that feedback-manager can learn from feedback in this Python environment.

Runs offline, in memory, and changes nothing. Submits two corrections, lets the
background learner turn them into a candidate artifact, reviews and activates
it, and checks that retrieval returns it only to the right tenant and agent:

    python scripts/verify_learning.py
    python scripts/verify_learning.py --json

Exit codes: 0 every check passes, 1 a check fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback
from typing import Any

LESSON = "Validate tool results before acting."


async def checks() -> list[dict[str, Any]]:
    from feedback_manager import (
        ExecutionContext,
        FeedbackManager,
        FeedbackTarget,
        FeedbackTargetType,
    )
    from feedback_manager.integrations.frameworks.custom import CustomAgentAdapter
    from feedback_manager.learning import ArtifactScope, ArtifactStatus
    from feedback_manager.learning.engine import LearningEngine
    from feedback_manager.retrieval import RetrievalRequest
    from feedback_manager.storage.artifacts import ArtifactQuery, InMemoryArtifactRepository

    results: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        results.append({"check": name, "passed": passed, "detail": detail})

    repository = InMemoryArtifactRepository()
    engine = LearningEngine(repository)
    scope = ArtifactScope(tenant_id="verify-tenant", agent_id="verify-agent")
    async with FeedbackManager(learning_engine=engine) as manager:
        for run_id in ("verify-run-1", "verify-run-2"):
            await manager.for_tenant(scope.tenant_id).submit(
                source="human",
                category="correction",
                target=FeedbackTarget(type=FeedbackTargetType.RUN, id=run_id),
                payload={"correction": LESSON},
                execution_context=ExecutionContext(agent_id="verify-agent", run_id=run_id),
                idempotency_key=run_id,
            )
        learner = manager.background_learner
        check("the background learner is enabled", learner is not None)
        if learner is None:
            return results
        processed = await learner.drain()
        check("the background learner processed both jobs", processed == 2, f"{processed}")

        artifacts = (await repository.query(ArtifactQuery(scope=scope))).artifacts
        check("two corrections became one artifact", len(artifacts) == 1, f"{len(artifacts)}")
        if len(artifacts) != 1:
            return results
        artifact = artifacts[0]
        check(
            "the artifact is a candidate with two pieces of evidence",
            artifact.status is ArtifactStatus.CANDIDATE and artifact.evidence_count == 2,
            f"{artifact.status.value}, evidence={artifact.evidence_count}",
        )
        request = RetrievalRequest(scope=scope, agent_id="verify-agent", task="validate results")
        before = await engine.retrieve(request)
        check("a candidate is not retrieved", not before.results)

        await engine.request_review(scope, artifact.artifact_id)
        await engine.review(scope, artifact.artifact_id, reviewer="verify", approved=True)
        artifact = await engine.activate(scope, artifact.artifact_id)
        check("review activates the artifact", artifact.status is ArtifactStatus.ACTIVE)

        context = await CustomAgentAdapter().context_injector(engine).build(request)
        check("the agent receives the lesson", context is not None and LESSON in context)

        other_agent = await engine.retrieve(
            request.model_copy(update={"agent_id": "another-agent"})
        )
        check("another agent receives nothing", not other_agent.results)
        other_tenant = await engine.retrieve(
            RetrievalRequest(
                scope=ArtifactScope(tenant_id="another-tenant", agent_id="verify-agent"),
                agent_id="verify-agent",
                task="validate results",
            )
        )
        check("another tenant receives nothing", not other_tenant.results)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--json", action="store_true", help="Print one JSON object to stdout.")
    arguments = parser.parse_args()
    try:
        results = asyncio.run(checks())
    except Exception as error:  # report the failure instead of a traceback
        detail = "".join(traceback.format_exception_only(error)).strip()
        results: list[dict[str, Any]] = [
            {"check": "the learning flow runs", "passed": False, "detail": detail}
        ]
    failed = [result for result in results if not result["passed"]]
    summary = "All checks passed." if not failed else f"{len(failed)} check(s) failed."
    if arguments.json:
        checks_out = [
            {
                "status": "PASS" if result["passed"] else "FAIL",
                "message": result["check"]
                + ("" if result["passed"] or not result["detail"] else f": {result['detail']}"),
            }
            for result in results
        ]
        print(json.dumps({"ok": not failed, "summary": summary, "checks": checks_out}, indent=2))
    else:
        for result in results:
            line = f"{'PASS' if result['passed'] else 'FAIL'}  {result['check']}"
            if not result["passed"] and result["detail"]:
                line += f"\n      {result['detail']}"
            print(line)
        print(summary)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
