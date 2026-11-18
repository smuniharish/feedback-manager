import asyncio
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from datetime import timedelta
from uuid import UUID

import pytest

from examples.sqlite_artifact_repository import SQLiteArtifactRepository
from feedback_manager.retrieval import RetrievalLoomRetriever, RetrievalRequest
from tests.learning import _processes
from tests.learning._processes import TENANT

pytestmark = pytest.mark.concurrency

REQUEST = RetrievalRequest(
    scope=TENANT, agent_id="agent", task="reconcile ledger", limit=20, max_candidates=50
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def in_processes(count: int, function, *arguments):
    """Run ``function`` in ``count`` concurrently spawned processes."""
    loop = asyncio.get_running_loop()
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=count, mp_context=context) as executor:
        return await asyncio.gather(
            *(loop.run_in_executor(executor, function, *args) for args in arguments)
        )


async def returned(retriever: RetrievalLoomRetriever) -> set[UUID]:
    return {item.artifact_id for item in (await retriever.retrieve(REQUEST)).artifacts}


async def test_owned_projection_catches_up_with_another_process_within_its_ttl(tmp_path):
    path = tmp_path / "artifacts.db"
    repository = SQLiteArtifactRepository(path)
    retriever = RetrievalLoomRetriever(repository, projection_ttl=timedelta(seconds=30))
    clock = Clock()
    retriever._clock = clock
    try:
        assert await returned(retriever) == set()

        [[published]] = await in_processes(1, _processes.publish, (str(path), ["reconcile ledger"]))
        artifact_id = UUID(published)
        # The other process consumed the shared outbox, so this engine got no intent.
        assert await repository.pending_projections(TENANT) == ()
        assert await returned(retriever) == set()
        clock.now += 30
        assert await returned(retriever) == {artifact_id}

        await in_processes(1, _processes.deprecate, (str(path), published))
        # Revalidation drops a superseded projection before any refresh.
        assert await returned(retriever) == set()
    finally:
        await retriever.aclose()
        await repository.close()


async def test_concurrent_outbox_consumers_in_separate_processes_converge(tmp_path):
    path = tmp_path / "artifacts.db"
    SQLiteArtifactRepository(path)._connection.close()
    batches = [[f"reconcile ledger {worker}-{index}" for index in range(3)] for worker in range(3)]
    published = await in_processes(
        3, _processes.publish, *((str(path), batch) for batch in batches)
    )
    expected = {UUID(item) for batch in published for item in batch}
    assert len(expected) == 9

    repository = SQLiteArtifactRepository(path)
    retriever = RetrievalLoomRetriever(repository)
    try:
        await retriever.drain_intents(TENANT)
        assert await repository.pending_projections(TENANT) == ()
        assert await returned(retriever) == expected
        for artifact_id in expected:
            history = await repository.versions(TENANT, artifact_id)
            assert [item.version for item in history] == [1, 2, 3, 4]
    finally:
        await retriever.aclose()
        await repository.close()
