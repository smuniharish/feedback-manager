"""Measure feedback-manager's overhead: latency percentiles and peak memory.

Each scenario times one operation many times, after untimed warmup calls, and
reports p50/p95/p99 wall time and the peak traced memory:

- ``submit``: one ``FeedbackManager.submit`` with automatic learning off.
- ``submit+learning``: the same call when it also enqueues a learning job.
- ``lifecycle``: ``acknowledge``, ``mark_handled`` and ``resolve`` of one event.
- ``retrieve:structured``: the learning engine's retrieval over active lessons.
- ``retrieve:hybrid``: hybrid retrieval over the same lessons, with LanceDB and
  the offline hashing embedding provider. It needs the ``lancedb`` extra and
  is skipped without it.

Everything runs in process with the in-memory stores and no observability
export, so no network or disk time is included except LanceDB's local files.
Numbers are machine-dependent; compare scenarios on the same machine.

Run with:

    uv run --no-sync python scripts/benchmark_overhead.py --runs 1000 --lessons 50
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import importlib.util
import statistics
import tempfile
import time
import tracemalloc
from dataclasses import dataclass
from functools import partial
from itertools import count
from typing import TYPE_CHECKING, Any

from feedback_manager import ExecutionContext, FeedbackManager, FeedbackTarget, FeedbackTargetType
from feedback_manager.learning import ArtifactScope
from feedback_manager.learning.engine import LearningEngine
from feedback_manager.observability import NoOpObservabilitySink
from feedback_manager.retrieval import RetrievalRequest
from feedback_manager.storage.artifacts import ArtifactQuery, InMemoryArtifactRepository

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator, Sequence
    from uuid import UUID

    from feedback_manager.retrieval.base import LearnedArtifactRetriever

TENANT = "bench"
AGENT = "bench-agent"
SCOPE = ArtifactScope(tenant_id=TENANT, agent_id=AGENT)


@dataclass(frozen=True, slots=True)
class Result:
    name: str
    p50_ms: float
    p95_ms: float
    p99_ms: float
    peak_kib: float


def _summary(name: str, samples: Sequence[float], peak: int) -> Result:
    cuts = statistics.quantiles(samples, n=100, method="inclusive")
    return Result(
        name,
        p50_ms=cuts[49] * 1_000,
        p95_ms=cuts[94] * 1_000,
        p99_ms=cuts[98] * 1_000,
        peak_kib=peak / 1024,
    )


async def _traced(name: str, call: Callable[[], Awaitable[Any]], runs: int, warmup: int) -> Result:
    for _ in range(warmup):
        await call()
    gc.collect()
    tracemalloc.start()
    try:
        samples = []
        for _ in range(runs):
            started = time.perf_counter()
            await call()
            samples.append(time.perf_counter() - started)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return _summary(name, samples, peak)


def _manager(*, learning_engine: LearningEngine | None = None) -> FeedbackManager:
    return FeedbackManager(
        observability_sink=NoOpObservabilitySink(),
        learning_engine=learning_engine,
        learning=learning_engine is not None,
    )


async def _submit(manager: FeedbackManager, number: int, text: str = "Check the input.") -> UUID:
    feedback = await manager.for_tenant(TENANT).submit(
        source="human",
        category="correction",
        target=FeedbackTarget(type=FeedbackTargetType.RUN, id=f"run-{number}"),
        payload={"correction": text},
        execution_context=ExecutionContext(agent_id=AGENT, run_id=f"run-{number}"),
        idempotency_key=f"correction:{number}",
    )
    return feedback.feedback_id


async def _submit_next(manager: FeedbackManager, numbers: Iterator[int]) -> UUID:
    return await _submit(manager, next(numbers))


async def submit_overhead(runs: int, warmup: int) -> list[Result]:
    """Time ``submit`` with automatic learning off, then on."""
    results = []
    for name, engine in (
        ("submit", None),
        ("submit+learning", LearningEngine(InMemoryArtifactRepository())),
    ):
        manager = _manager(learning_engine=engine)
        try:
            submit = partial(_submit_next, manager, count())
            results.append(await _traced(name, submit, runs, warmup))
            if manager.background_learner is not None:
                await manager.background_learner.drain()
        finally:
            await manager.aclose()
    return results


async def lifecycle_overhead(runs: int, warmup: int) -> Result:
    """Time moving one event from received to resolved."""
    manager = _manager()
    try:
        pending = iter([await _submit(manager, number) for number in range(runs + warmup)])

        async def resolve() -> None:
            feedback_id = next(pending)
            await manager.acknowledge(feedback_id)
            await manager.mark_handled(feedback_id)
            await manager.resolve(feedback_id, resolution={"outcome": "fixed"})

        return await _traced("lifecycle", resolve, runs, warmup)
    finally:
        await manager.aclose()


async def _learn(lessons: int) -> tuple[InMemoryArtifactRepository, LearningEngine]:
    """Build ``lessons`` reviewed, active lessons through the public workflow."""
    repository = InMemoryArtifactRepository()
    engine = LearningEngine(repository)
    manager = _manager(learning_engine=engine)
    try:
        numbers = count()
        for lesson in range(lessons):
            for _ in range(2):
                await _submit(manager, next(numbers), f"Verify field {lesson} before acting.")
        assert manager.background_learner is not None
        await manager.background_learner.drain()
    finally:
        await manager.aclose()
    page = await repository.query(ArtifactQuery(scope=SCOPE, limit=lessons))
    for artifact in page.artifacts:
        await engine.request_review(SCOPE, artifact.artifact_id)
        await engine.review(SCOPE, artifact.artifact_id, reviewer="bench", approved=True)
        await engine.activate(SCOPE, artifact.artifact_id)
    return repository, engine


async def _retrieve(retriever: LearnedArtifactRetriever, request: RetrievalRequest) -> None:
    result = await retriever.retrieve(request)
    if not result.artifacts:
        raise RuntimeError("retrieval returned no lessons")


async def retrieval_overhead(runs: int, warmup: int, lessons: int) -> list[Result]:
    """Time structured retrieval and, with LanceDB installed, hybrid retrieval."""
    repository, engine = await _learn(lessons)
    request = RetrievalRequest(scope=SCOPE, agent_id=AGENT, task="verify field 7")
    results = [
        await _traced("retrieve:structured", lambda: _retrieve(engine, request), runs, warmup)
    ]
    if importlib.util.find_spec("lancedb") is None:
        return results

    from feedback_manager.embeddings import HashingEmbeddingProvider
    from feedback_manager.retrieval import ArtifactIndexer, HybridRetriever
    from feedback_manager.storage.vector.lancedb import LanceDBVectorStore

    with tempfile.TemporaryDirectory() as directory:
        vectors = await LanceDBVectorStore.open(directory)
        provider = HashingEmbeddingProvider()
        await ArtifactIndexer(repository, vectors, provider).reindex(SCOPE)
        hybrid = HybridRetriever(repository, vectors, provider)
        results.append(
            await _traced("retrieve:hybrid", lambda: _retrieve(hybrid, request), runs, warmup)
        )
    return results


def _table(results: Sequence[Result]) -> str:
    lines = [f"{'scenario':<22} {'p50 ms':>9} {'p95 ms':>9} {'p99 ms':>9} {'peak KiB':>10}"]
    lines.extend(
        f"{result.name:<22} {result.p50_ms:>9.3f} {result.p95_ms:>9.3f} "
        f"{result.p99_ms:>9.3f} {result.peak_kib:>10,.0f}"
        for result in results
    )
    return "\n".join(lines)


async def main(runs: int = 500, warmup: int = 20, lessons: int = 50) -> list[Result]:
    """Run every scenario, print one table, and return the results."""
    results = [
        *await submit_overhead(runs, warmup),
        await lifecycle_overhead(runs, warmup),
        *await retrieval_overhead(runs, warmup, lessons),
    ]
    print(_table(results))
    return results


def _parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", type=int, default=500, help="timed calls per scenario")
    parser.add_argument("--warmup", type=int, default=20, help="untimed calls first")
    parser.add_argument("--lessons", type=int, default=50, help="active lessons to retrieve from")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = _parse()
    asyncio.run(main(arguments.runs, arguments.warmup, arguments.lessons))
