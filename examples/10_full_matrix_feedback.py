"""Example 10 -- exercise every (source, category, target type) combination.

The Streamlit UI (``examples/streamlit_feedback_ui.py``) demonstrates a
*human* clicking through a handful of representative submissions. That is
not the same as proving the pipeline actually accepts every value of every
open, extensible identifier feedback-manager defines:

    - :class:`~feedback_manager.core.sources.FeedbackSource` (8 well-known values)
    - :class:`~feedback_manager.core.categories.FeedbackCategory` (15 well-known values)
    - :class:`~feedback_manager.core.targets.FeedbackTargetType` (13 well-known values)

This script submits one real ``FeedbackEvent`` for *every* combination of
those three -- 8 x 15 x 13 = 1560 real submissions -- against the same real
PostgreSQL store used by the other examples (falls back to
``InMemoryFeedbackStore`` if ``FEEDBACK_MANAGER_POSTGRES_DSN`` is unset), and
then queries the database back to prove every well-known value of every
axis actually landed, with the expected row count.

Run with::

    uv run python examples/10_full_matrix_feedback.py

(Set ``FEEDBACK_MANAGER_POSTGRES_DSN`` first to verify against real
PostgreSQL instead of the in-memory store.)
"""

from __future__ import annotations

import asyncio
import itertools
import os
import selectors
import sys
import time
from collections import Counter

from feedback_manager import (
    FeedbackCategory,
    FeedbackManager,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)

# The full well-known set for each axis (see core/sources.py, core/categories.py,
# core/targets.py) -- each of these types is an open ``str`` subclass, so this
# is "every value the package ships a named constant for", not a closed enum.
ALL_SOURCES: list[FeedbackSource] = [
    FeedbackSource.HUMAN,
    FeedbackSource.AGENT,
    FeedbackSource.GENERATION,
    FeedbackSource.TOOL,
    FeedbackSource.EVALUATOR,
    FeedbackSource.APPLICATION,
    FeedbackSource.SYSTEM,
    FeedbackSource.EXTERNAL,
]

ALL_CATEGORIES: list[FeedbackCategory] = [
    FeedbackCategory.APPROVAL,
    FeedbackCategory.REJECTION,
    FeedbackCategory.CORRECTION,
    FeedbackCategory.RATING,
    FeedbackCategory.COMMENT,
    FeedbackCategory.INTERRUPTION,
    FeedbackCategory.CANCELLATION,
    FeedbackCategory.FAILURE,
    FeedbackCategory.TIMEOUT,
    FeedbackCategory.VALIDATION,
    FeedbackCategory.QUALITY,
    FeedbackCategory.UNCERTAINTY,
    FeedbackCategory.REQUEST_FOR_HUMAN,
    FeedbackCategory.PARTIAL_RESULT,
    FeedbackCategory.COMPLETION,
]

ALL_TARGET_TYPES: list[FeedbackTargetType] = [
    FeedbackTargetType.APPLICATION,
    FeedbackTargetType.AGENT,
    FeedbackTargetType.GRAPH,
    FeedbackTargetType.THREAD,
    FeedbackTargetType.RUN,
    FeedbackTargetType.CHECKPOINT,
    FeedbackTargetType.NODE,
    FeedbackTargetType.TASK,
    FeedbackTargetType.TOOL_CALL,
    FeedbackTargetType.TOOL_RESULT,
    FeedbackTargetType.GENERATION,
    FeedbackTargetType.MESSAGE,
    FeedbackTargetType.STATE,
]


def _run(coro):
    """Windows + psycopg async needs a selector-based event loop (see example 1)."""
    if sys.platform == "win32":
        return asyncio.run(
            coro, loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        )
    return asyncio.run(coro)


async def _build_manager() -> FeedbackManager:
    dsn = os.environ.get("FEEDBACK_MANAGER_POSTGRES_DSN")
    if dsn:
        from postgres_feedback_store import PostgresFeedbackStore

        store = await PostgresFeedbackStore.connect(dsn)
        return FeedbackManager(store=store)
    return FeedbackManager()


async def main() -> None:
    manager = await _build_manager()
    backend = "PostgreSQL" if os.environ.get("FEEDBACK_MANAGER_POSTGRES_DSN") else "in-memory"

    combinations = list(itertools.product(ALL_SOURCES, ALL_CATEGORIES, ALL_TARGET_TYPES))
    print(f"Backend: {backend}")
    print(
        f"Matrix: {len(ALL_SOURCES)} sources x {len(ALL_CATEGORIES)} categories x "
        f"{len(ALL_TARGET_TYPES)} target types = {len(combinations)} combinations"
    )

    semaphore = asyncio.Semaphore(25)
    seen_sources: Counter[str] = Counter()
    seen_categories: Counter[str] = Counter()
    seen_target_types: Counter[str] = Counter()

    async def submit_one(
        source: FeedbackSource, category: FeedbackCategory, target_type: FeedbackTargetType
    ) -> None:
        async with semaphore:
            target_id = f"matrix::{source}::{category}::{target_type}"
            await manager.submit(
                source=source,
                category=category,
                target=FeedbackTarget(type=target_type, id=target_id),
                payload={"matrix_probe": True},
                metadata={
                    "source": str(source),
                    "category": str(category),
                    "target_type": str(target_type),
                },
            )
        seen_sources[str(source)] += 1
        seen_categories[str(category)] += 1
        seen_target_types[str(target_type)] += 1

    started = time.perf_counter()
    await asyncio.gather(*(submit_one(*combo) for combo in combinations))
    elapsed = time.perf_counter() - started

    print(f"\nSubmitted {len(combinations)} real feedback events in {elapsed:.2f}s")
    print(f"Distinct sources observed:      {len(seen_sources)} / {len(ALL_SOURCES)}")
    print(f"Distinct categories observed:    {len(seen_categories)} / {len(ALL_CATEGORIES)}")
    print(f"Distinct target types observed:  {len(seen_target_types)} / {len(ALL_TARGET_TYPES)}")
    assert len(seen_sources) == len(ALL_SOURCES)
    assert len(seen_categories) == len(ALL_CATEGORIES)
    assert len(seen_target_types) == len(ALL_TARGET_TYPES)

    all_events = await manager.list()
    matrix_events = [e for e in all_events if e.payload.get("matrix_probe")]
    print(
        f"\nReal events re-read back from the store: {len(matrix_events)} (expected {len(combinations)})"
    )
    assert len(matrix_events) == len(combinations)

    distinct_triples = {(e.source, e.category, e.target.type) for e in matrix_events}
    print(f"Distinct (source, category, target_type) triples persisted: {len(distinct_triples)}")
    assert len(distinct_triples) == len(combinations)
    print("Every combination round-tripped through the real pipeline exactly once.")


if __name__ == "__main__":
    _run(main())
