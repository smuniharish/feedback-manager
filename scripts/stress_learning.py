"""Load- and failure-test background learning across processes on one host.

The harness seeds feedback events, each with an independently supported and
accepted assessment, and enqueues one assessment learning job per event. It then
runs worker processes that each open their own connections to the shared
SQLite feedback, experience, assessment, artifact and job files and drain the
queue concurrently. Few topics mean many events merge into the same artifact,
so the workers contend on compare-and-set artifact updates.

With ``--crash``, one worker first runs alone and hard-exits (``os._exit``)
after it has written an artifact but before it completes the job, so its lease
is left behind. The other workers must reclaim the job after the lease expires
and learn it again without duplicating evidence.

Afterwards the parent drains anything left over and checks these invariants:

- every job succeeded; none is pending, running or dead-lettered;
- every feedback event is evidence exactly once, across one artifact per topic;
- with ``--crash``, the crashed job was reclaimed and the worker exited as killed.

A JSON summary with throughput is printed. Exit status 1 means an invariant
failed. It exercises one host only: SQLite serializes writers, so multi-host
deployments need networked stores and their own load tests.

Run with:

    uv run --no-sync python scripts/stress_learning.py --events 200 --workers 4 --crash
"""

from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples.sqlite_artifact_repository import SQLiteArtifactRepository  # noqa: E402
from examples.sqlite_feedback_store import SQLiteFeedbackStore  # noqa: E402
from feedback_manager import (  # noqa: E402
    ExecutionContext,
    FeedbackManager,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.assessment import (  # noqa: E402
    AssessmentEvidence,
    AssessmentStatus,
    ConservativeFeedbackCritic,
    LearningDecision,
)
from feedback_manager.experience import ExecutionExperience  # noqa: E402
from feedback_manager.learning import (  # noqa: E402
    ArtifactScope,
    BackgroundLearner,
    ContextScopeResolver,
    RetryPolicy,
)
from feedback_manager.learning.assessment_verification import AssessmentVerifier  # noqa: E402
from feedback_manager.learning.engine import LearningEngine  # noqa: E402
from feedback_manager.learning.jobs import (  # noqa: E402
    LearningJob,
    LearningJobStatus,
    SQLiteLearningJobStore,
)
from feedback_manager.storage.artifacts import ArtifactQuery  # noqa: E402
from feedback_manager.storage.assessment import SQLiteAssessmentRepository  # noqa: E402
from feedback_manager.storage.experience import SQLiteExperienceRepository  # noqa: E402
from feedback_manager.storage.payload import PayloadScope  # noqa: E402

SCOPE = ArtifactScope(tenant_id="tenant", agent_id="agent")
CRASH_EXIT = 137
_RETRY = RetryPolicy(
    max_attempts=10, initial_delay=timedelta(milliseconds=50), max_delay=timedelta(seconds=1)
)


class _CrashingJobStore(SQLiteLearningJobStore):
    """Hard-exit where a worker would record a learned job as complete."""

    async def complete(self, job_id: Any, *, attempt: int) -> LearningJob:
        os._exit(CRASH_EXIT)


@dataclass
class _Stores:
    feedback: SQLiteFeedbackStore
    artifacts: SQLiteArtifactRepository
    jobs: SQLiteLearningJobStore
    experiences: SQLiteExperienceRepository
    assessments: SQLiteAssessmentRepository
    manager: FeedbackManager
    learner: BackgroundLearner

    async def close(self) -> None:
        await self.jobs.close()
        await self.artifacts.close()
        await self.experiences.close()
        await self.assessments.close()


def _open(directory: Path, *, lease: float, crash: bool = False) -> _Stores:
    feedback = SQLiteFeedbackStore(directory / "feedback.db")
    artifacts = SQLiteArtifactRepository(directory / "artifacts.db")
    jobs = (_CrashingJobStore if crash else SQLiteLearningJobStore)(directory / "jobs.db")
    experiences = SQLiteExperienceRepository(directory / "experiences.db")
    assessments = SQLiteAssessmentRepository(directory / "assessments.db")
    manager = FeedbackManager(store=feedback, learning=False)
    engine = LearningEngine(
        artifacts, assessment_verifier=AssessmentVerifier(feedback, experiences, assessments)
    )
    learner = BackgroundLearner(
        manager,
        engine,
        jobs,
        scope_resolver=ContextScopeResolver(namespace="default"),
        retry=_RETRY,
        batch_size=5,
        lease=timedelta(seconds=lease),
        change_source=feedback,
        require_durable=True,
    )
    return _Stores(feedback, artifacts, jobs, experiences, assessments, manager, learner)


async def _seed_one(stores: _Stores, index: int, topic: str) -> None:
    run = f"run-{index}"
    event = await stores.manager.submit(
        source="human",
        category="correction",
        target=FeedbackTarget(type=FeedbackTargetType.RUN, id=run),
        payload={"correction": topic},
        execution_context=ExecutionContext(tenant_id="tenant", agent_id="agent", run_id=run),
    )
    scope = PayloadScope.model_validate(SCOPE.model_dump())
    experience = ExecutionExperience(scope=scope, execution_id=run)
    supporting = ExecutionExperience(scope=scope, execution_id=f"independent-{index}")
    await stores.experiences.create(experience)
    await stores.experiences.create(supporting)
    evidence = AssessmentEvidence(
        evidence_id=f"independent-{index}",
        scope=scope,
        experience_id=supporting.experience_id,
        experience_version=supporting.version,
    )
    assessment = (
        await ConservativeFeedbackCritic().assess(event, experience, supporting=(evidence,))
    ).model_copy(update={"status": AssessmentStatus.SUPPORTED})
    await stores.assessments.create(assessment)
    await stores.assessments.decide(
        LearningDecision(
            scope=scope,
            assessment_id=assessment.assessment_id,
            assessment_version=assessment.version,
            accepted=True,
            reviewer="reviewer",
        )
    )
    await stores.learner.enqueue_assessment(
        event.feedback_id, assessment.assessment_id, assessment_version=assessment.version
    )


async def _drain_until_settled(stores: _Stores, deadline: float) -> int:
    """Drain, waiting out retry delays and expired leases, until no work remains."""
    processed = 0
    while True:
        processed += await stores.learner.drain()
        counts = await stores.jobs.counts()
        if not counts.get(LearningJobStatus.PENDING) and not counts.get(LearningJobStatus.RUNNING):
            return processed
        if time.monotonic() > deadline:
            raise TimeoutError("learning jobs did not settle before the deadline")
        await asyncio.sleep(0.05)


async def _work(directory: Path, lease: float, crash: bool, deadline: float) -> dict[str, Any]:
    stores = _open(directory, lease=lease, crash=crash)
    try:
        processed = await _drain_until_settled(stores, deadline)
        stats = stores.learner.stats
        return {
            "processed": processed,
            "succeeded": stats.succeeded,
            "retried": stats.retried,
            "dead_lettered": stats.dead_lettered,
            "lease_lost": stats.lease_lost,
            "errors": stats.errors,
        }
    finally:
        await stores.close()


def work(directory: str, lease: float, crash: bool, deadline: float, name: str) -> None:
    """Process entry point: one worker with its own store connections."""
    report = asyncio.run(_work(Path(directory), lease, crash, deadline))
    (Path(directory) / f"{name}.json").write_text(json.dumps(report), encoding="utf-8")


def _spawn(
    context: Any, directory: Path, lease: float, deadline: float, name: str, *, crash: bool
) -> Any:
    process = context.Process(
        target=work, args=(str(directory), lease, crash, deadline, name), name=name
    )
    process.start()
    return process


async def _verify(stores: _Stores, events: int, topics: int, crash: bool) -> dict[str, Any]:
    failures: list[str] = []
    counts = await stores.jobs.counts()
    if counts.get(LearningJobStatus.SUCCEEDED, 0) != events or sum(counts.values()) != events:
        failures.append(f"job counts {dict(counts)} != {events} succeeded")
    jobs = await stores.jobs.list(status=LearningJobStatus.SUCCEEDED, limit=events + 1)
    reclaimed = sum(job.attempts > 1 for job in jobs)
    if crash and reclaimed < 1:
        failures.append("the crashed job was not reclaimed")
    page = await stores.artifacts.query(ArtifactQuery(scope=SCOPE, limit=topics + 1))
    if len(page.artifacts) != topics:
        failures.append(f"{len(page.artifacts)} artifacts != {topics} topics")
    feedback_ids = [item.feedback_id for artifact in page.artifacts for item in artifact.evidence]
    seeded = {event.feedback_id for event in await stores.manager.query()}
    if len(feedback_ids) != len(set(feedback_ids)):
        failures.append("feedback evidence was duplicated")
    if set(feedback_ids) != seeded or len(seeded) != events:
        failures.append("learned evidence does not match the seeded feedback")
    return {
        "failures": failures,
        "reclaimed_jobs": reclaimed,
        "attempts": sum(job.attempts for job in jobs),
        "artifacts": len(page.artifacts),
    }


async def _seed(directory: Path, events: int, topics: int, lease: float) -> None:
    stores = _open(directory, lease=lease)
    try:
        for index in range(events):
            await _seed_one(stores, index, f"Validate tool results before step {index % topics}")
    finally:
        await stores.close()


async def _finish(
    directory: Path, events: int, topics: int, lease: float, crash: bool, deadline: float
) -> dict[str, Any]:
    stores = _open(directory, lease=lease)
    try:
        recovered = await _drain_until_settled(stores, deadline)
        return {"recovered_by_parent": recovered, **await _verify(stores, events, topics, crash)}
    finally:
        await stores.close()


def run(
    directory: Path,
    *,
    events: int,
    workers: int,
    topics: int,
    lease: float,
    crash: bool,
    timeout: float,
) -> dict[str, Any]:
    """Seed, run workers, recover and verify; return the summary."""
    asyncio.run(_seed(directory, events, topics, lease))
    context = multiprocessing.get_context("spawn")
    deadline = time.monotonic() + timeout
    exit_codes: dict[str, int | None] = {}
    started = time.perf_counter()
    if crash:
        victim = _spawn(context, directory, lease, deadline, "crashing-worker", crash=True)
        victim.join(timeout)
        exit_codes[victim.name] = victim.exitcode
    processes = [
        _spawn(context, directory, lease, deadline, f"worker-{index}", crash=False)
        for index in range(workers)
    ]
    for process in processes:
        process.join(max(deadline - time.monotonic(), 1))
        if process.is_alive():
            process.kill()
            process.join()
        exit_codes[process.name] = process.exitcode
    elapsed = time.perf_counter() - started
    summary = asyncio.run(_finish(directory, events, topics, lease, crash, deadline + lease))
    failures: list[str] = summary.pop("failures")
    if crash and exit_codes.get("crashing-worker") != CRASH_EXIT:
        failures.append("the crashing worker did not crash after learning")
    if any(exit_codes[f"worker-{index}"] != 0 for index in range(workers)):
        failures.append("a worker failed")
    reports = [
        json.loads(path.read_text(encoding="utf-8")) for path in directory.glob("worker-*.json")
    ]
    return {
        "events": events,
        "workers": workers,
        "topics": topics,
        "crash": crash,
        "seconds": round(elapsed, 3),
        "jobs_per_second": round(events / elapsed, 1),
        "worker_exit_codes": exit_codes,
        "retried": sum(report["retried"] for report in reports),
        "dead_lettered": sum(report["dead_lettered"] for report in reports),
        "lease_lost": sum(report["lease_lost"] for report in reports),
        **summary,
        "failures": failures,
        "ok": not failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--events", type=int, default=100)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--topics", type=int, default=5)
    parser.add_argument("--lease", type=float, default=2.0, help="lease in seconds")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--crash", action="store_true", help="crash one worker mid-job")
    parser.add_argument("--directory", type=Path, help="keep the databases here")
    args = parser.parse_args(argv)
    if args.events < 1 or args.workers < 1 or not 1 <= args.topics <= args.events:
        parser.error("need events >= topics >= 1 and workers >= 1")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        directory = args.directory or Path(scratch)
        directory.mkdir(parents=True, exist_ok=True)
        summary = run(
            directory,
            events=args.events,
            workers=args.workers,
            topics=args.topics,
            lease=args.lease,
            crash=args.crash,
            timeout=args.timeout,
        )
    print(json.dumps(summary, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
