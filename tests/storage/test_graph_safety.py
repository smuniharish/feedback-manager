import asyncio
import threading

import pytest

from feedback_manager.learning import LearningError, LearningValidationError, TenantScope
from feedback_manager.storage.graph import GraphRelationship, memory

SCOPE = TenantScope(tenant_id="tenant")


def test_traversal_work_is_bounded(monkeypatch):
    monkeypatch.setattr(memory, "MAX_TRAVERSAL_NODES", 2)
    with pytest.raises(LearningValidationError, match="node safety"):
        memory.breadth_first(["a", "b", "c"], lambda _: [], 1)
    edges = [
        GraphRelationship(
            relationship_id=f"edge-{number}",
            scope=SCOPE,
            source_id="a",
            target_id=f"target-{number}",
            relationship_type="related",
        )
        for number in range(3)
    ]
    with pytest.raises(LearningValidationError, match="node safety"):
        memory.breadth_first(["a"], lambda _: edges, 1)
    monkeypatch.setattr(memory, "MAX_TRAVERSAL_NODES", 100)
    monkeypatch.setattr(memory, "MAX_TRAVERSAL_EDGES", 1)
    with pytest.raises(LearningValidationError, match="relationship safety"):
        memory.breadth_first(["a"], lambda _: edges, 1)


def _ladybug_store(path):
    pytest.importorskip("ladybug")
    from feedback_manager.storage.graph.ladybug import LadybugGraphStore

    return LadybugGraphStore(path)


async def test_cancellation_keeps_connection_locked_until_thread_exits(tmp_path):
    store = _ladybug_store(tmp_path / "graph.db")
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    def blocked():
        loop.call_soon_threadsafe(entered.set)
        if not release.wait(5):
            raise TimeoutError("test release was not signalled")

    task = asyncio.create_task(store._run(blocked))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        await asyncio.sleep(0)
        assert store._lock.locked()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await store.health()
        await store.aclose()
        await store.aclose()
    finally:
        release.set()
        store.close()


async def test_queued_operation_checks_closed_state(tmp_path):
    store = _ladybug_store(tmp_path / "graph.db")
    await store._lock.acquire()
    task = asyncio.create_task(store.health())
    await asyncio.sleep(0)
    store.close()
    store._lock.release()
    with pytest.raises(LearningError, match="closed"):
        await task
