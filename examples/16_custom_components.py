"""Bring your own components, from agxai's provenance store down to the graph database.

Run with feedback-manager[lancedb,ladybugdb]. Every collaborator below is the
caller's own: a feedback store subclass, a function subscriber, a scope
resolver function, an agxai provenance store, a LanceDB connection, and a
LadybugDB database. The feedback store is then certified with the public
contract kit, exactly as a pytest run would.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import ladybug
import lancedb
from agxai import InMemoryProvenanceStore

from feedback_manager import FeedbackEvent, FeedbackManager, FeedbackTarget
from feedback_manager.integrations.provenance import (
    AgxaiProvenanceProvider,
    create_agxai_runtime,
)
from feedback_manager.learning import ArtifactScope, TenantScope
from feedback_manager.storage import InMemoryFeedbackStore
from feedback_manager.storage.graph import GraphNode
from feedback_manager.storage.graph.ladybug import LadybugGraphStore
from feedback_manager.storage.vector.lancedb import LanceDBVectorStore
from feedback_manager.testing import FeedbackStoreContract


class CountingFeedbackStore(InMemoryFeedbackStore):
    """Stands in for your database: counts writes and delegates storage."""

    def __init__(self) -> None:
        super().__init__()
        self.writes = 0

    async def create(self, feedback: FeedbackEvent) -> FeedbackEvent:
        self.writes += 1
        return await super().create(feedback)


class TestCountingFeedbackStore(FeedbackStoreContract):
    async def make_store(self) -> CountingFeedbackStore:
        return CountingFeedbackStore()


def scope_for(feedback: FeedbackEvent) -> ArtifactScope | None:
    """Learn per agent; feedback without an agent is not learned from."""
    context = feedback.execution_context
    if context is None or context.agent_id is None:
        return None
    return ArtifactScope(tenant_id=context.tenant_id or "default", agent_id=context.agent_id)


async def run(directory: Path) -> None:
    provenance_store = InMemoryProvenanceStore()
    runtime = create_agxai_runtime(application_id="support", store=provenance_store)
    provider = AgxaiProvenanceProvider(runtime)
    print(f"agxai store is the caller's: {provider.store is provenance_store}")

    store = CountingFeedbackStore()
    seen: list[str] = []

    async def on_change(feedback: FeedbackEvent) -> None:
        seen.append(feedback.status.value)

    manager = FeedbackManager(
        store=store, provenance_provider=provider, learning_scope_resolver=scope_for
    )
    manager.subscribe(on_change)
    async with manager:
        feedback = await manager.submit(
            source="human",
            category="correction",
            target=FeedbackTarget(type="generation", id="gen-1"),
            payload={"corrected_text": "Canberra"},
        )
        await manager.acknowledge(feedback.feedback_id)
    print(f"Feedback writes: {store.writes}; subscriber saw: {seen}")

    connection = await lancedb.connect_async(str(directory / "vectors"))
    vectors = LanceDBVectorStore(str(directory / "vectors"), connection=connection)
    await vectors.health()
    print("LanceDB vector store is healthy on the caller's connection")

    database = ladybug.Database(str(directory / "graph.lbug"))
    graph = LadybugGraphStore(database=database)
    await graph.upsert_node(
        GraphNode(node_id="gen-1", scope=TenantScope(tenant_id="support"), node_type="Run")
    )
    graph.close()
    print(f"Injected LadybugDB database still open after store.close(): {not database.is_closed}")
    database.close()


def certify() -> int:
    """Run every contract test on the store and return how many passed."""
    kit = TestCountingFeedbackStore()
    tests = sorted(name for name in dir(kit) if name.startswith("test_"))
    for name in tests:
        getattr(kit, name)()
    return len(tests)


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        asyncio.run(run(Path(directory)))
    print(f"Certified CountingFeedbackStore: {certify()} contract tests passed")


if __name__ == "__main__":
    main()
