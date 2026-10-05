"""Submit one event for every (source, category, target type) combination.

Proves the whole pipeline accepts every well-known value of each open value
type: 8 sources x 15 categories x 13 target types = 1560 submissions, run
concurrently, then read back and checked. Uses the in-memory store, or, when
``FEEDBACK_MANAGER_POSTGRES_DSN`` is set, PostgreSQL in a separate
``feedback_matrix`` table, so these synthetic probes never mix with real
feedback.

Run with:

    uv run python examples/10_full_matrix_feedback.py
"""

import asyncio
import itertools
import os
import time

from postgres_feedback_store import DSN_VARIABLE, PostgresFeedbackStore, run

from feedback_manager import (
    FeedbackCategory,
    FeedbackManager,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)


async def submit_matrix(manager: FeedbackManager, run_tag: str) -> int:
    combinations = list(
        itertools.product(
            FeedbackSource.known_values(),
            FeedbackCategory.known_values(),
            FeedbackTargetType.known_values(),
        )
    )
    limit = asyncio.Semaphore(25)

    async def submit(source: str, category: str, target_type: str) -> None:
        async with limit:
            await manager.submit(
                source=source,
                category=category,
                target=FeedbackTarget(type=target_type, id=f"{run_tag}:{source}:{category}"),
                payload={"probe": True},
                feedback_type=run_tag,
            )

    await asyncio.gather(*(submit(*combination) for combination in combinations))
    return len(combinations)


async def main() -> None:
    dsn = os.environ.get(DSN_VARIABLE)
    store = await PostgresFeedbackStore.open(dsn, table="feedback_matrix") if dsn else None
    manager = FeedbackManager(store=store)
    run_tag = f"matrix-{time.time_ns()}"
    print(f"Store: {'PostgreSQL' if store else 'in-memory'}")

    started = time.perf_counter()
    submitted = await submit_matrix(manager, run_tag)
    elapsed = time.perf_counter() - started
    print(f"Submitted {submitted} events in {elapsed:.2f}s")

    stored = [event for event in await manager.query() if event.feedback_type == run_tag]
    triples = {(event.source, event.category, event.target.type) for event in stored}
    axes = (
        ("sources", FeedbackSource.known_values()),
        ("categories", FeedbackCategory.known_values()),
        ("target types", FeedbackTargetType.known_values()),
    )
    for index, (name, values) in enumerate(axes):
        print(f"Distinct {name}: {len({triple[index] for triple in triples})} of {len(values)}")
    print(f"Distinct combinations stored: {len(triples)} of {submitted}")
    assert len(stored) == len(triples) == submitted
    if store is not None:
        await store.close()


if __name__ == "__main__":
    run(main())
